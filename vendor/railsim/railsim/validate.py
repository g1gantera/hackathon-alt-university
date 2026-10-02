"""Проверка конфигурации: что не заполнено и что заполнено с ошибкой."""
from .hazards import HAZARD_KINDS
from .util import FEATURES

# Параметры, которые можно оставить пустыми
OPTIONAL = {"PLOT_HOURS", "WM_DISTANCES_KM"}
OPTIONAL_KEYS = {"fixed_departure_hours", "departure_hour_weights"}

# Группы параметров, которые нужны только при включённом флаге
GROUPS = {
    "USE_BORDER": ("BORDER_",),
    "USE_PORT": ("PORT_", "FERRY_", "SLOT_"),
    "USE_WAGON_MODULE": ("WM_",),
    "USE_CIS_EXCHANGE": ("CIS_",),
}


def _empty(v):
    return v is None or (isinstance(v, (list, dict, tuple, str)) and len(v) == 0)


def _walk(name, value, out):
    if _empty(value):
        out.append(name)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k in OPTIONAL_KEYS:
                continue
            _walk(f"{name}[{k!r}]", v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, (dict, list)):
                _walk(f"{name}[{i}]", v, out)
            elif v is None:
                out.append(f"{name}[{i}]")


def find_missing(cfg):
    names = [n for n in vars(cfg) if n.isupper() and not n.startswith("_")]  # порядок как в файле
    skip_prefixes = []
    missing = []
    for flag, prefixes in GROUPS.items():
        val = getattr(cfg, flag, None)
        if val is None:
            missing.append(flag)
        if not val:
            skip_prefixes.extend(prefixes)
    for n in names:
        if n in GROUPS or n in OPTIONAL:
            continue
        if any(n.startswith(p) for p in skip_prefixes):
            continue
        _walk(n, getattr(cfg, n), missing)
    # trains_per_day не нужен, если задано фиксированное расписание
    flows = getattr(cfg, "TRAIN_FLOWS", None) or []
    for i, f in enumerate(flows):
        if isinstance(f, dict) and f.get("fixed_departure_hours"):
            name = f"TRAIN_FLOWS[{i}]['trains_per_day']"
            if name in missing:
                missing.remove(name)
    return missing


def find_errors(cfg):
    """Логические проверки (запускаются, когда всё заполнено)."""
    err = []
    st = cfg.STATIONS
    names = [s["name"] for s in st]
    if len(st) < 2:
        err.append("STATIONS: нужно минимум 2 станции")
    if len(set(names)) != len(names):
        err.append("STATIONS: названия станций должны быть уникальны")
    kms = [float(s["km"]) for s in st]
    if any(b <= a for a, b in zip(kms, kms[1:])):
        err.append("STATIONS: км должен строго возрастать")
    if len(cfg.SEGMENTS) != len(st) - 1:
        err.append(f"SEGMENTS: нужно {len(st) - 1} перегонов (станций {len(st)}), сейчас {len(cfg.SEGMENTS)}")
    for i, s in enumerate(cfg.SEGMENTS):
        if s["tracks"] not in (1, 2):
            err.append(f"SEGMENTS[{i}]['tracks']: 1 или 2")
        if s["blocking"] not in ("auto", "semi_auto"):
            err.append(f"SEGMENTS[{i}]['blocking']: 'auto' или 'semi_auto'")
        if float(s["max_speed_kmh"]) <= 0:
            err.append(f"SEGMENTS[{i}]['max_speed_kmh'] должна быть > 0")
    for i, f in enumerate(cfg.TRAIN_FLOWS):
        if f["type"] not in cfg.TRAIN_TYPES:
            err.append(f"TRAIN_FLOWS[{i}]['type']: нет типа {f['type']!r} в TRAIN_TYPES")
        for k in ("origin", "destination"):
            if f[k] not in names:
                err.append(f"TRAIN_FLOWS[{i}]['{k}']: нет станции {f[k]!r}")
        if f["origin"] == f["destination"]:
            err.append(f"TRAIN_FLOWS[{i}]: станции отправления и назначения совпадают")
        w = f.get("departure_hour_weights")
        if w and (len(w) != 24 or sum(w) <= 0):
            err.append(f"TRAIN_FLOWS[{i}]['departure_hour_weights']: нужно 24 неотрицательных веса")
    if set(cfg.HAZARDS) != set(HAZARD_KINDS):
        err.append(f"HAZARDS: нужны ключи {HAZARD_KINDS}")
    for k, p in cfg.HAZARDS.items():
        if not p["closure"] and not (0 < float(p["speed_factor"]) <= 1):
            err.append(f"HAZARDS[{k!r}]['speed_factor'] должен быть в (0, 1]")
    for sc in ("baseline", "auto"):
        if set(cfg.DETECTION_PROB[sc]) != set(HAZARD_KINDS):
            err.append(f"DETECTION_PROB[{sc!r}]: нужны ключи {HAZARD_KINDS}")
    unknown = set(cfg.AUTO_FEATURES) - set(FEATURES)
    if unknown:
        err.append(f"AUTO_FEATURES: неизвестные функции {unknown}")
    if cfg.USE_BORDER and cfg.BORDER_STATION not in names:
        err.append(f"BORDER_STATION: нет станции {cfg.BORDER_STATION!r}")
    if cfg.USE_PORT and cfg.PORT_STATION not in names:
        err.append(f"PORT_STATION: нет станции {cfg.PORT_STATION!r}")
    if cfg.USE_PORT and float(cfg.FERRY_INTERVAL_H) <= 0:
        err.append("FERRY_INTERVAL_H должен быть > 0")
    _check_probs(cfg, err)
    return err


def _check_probs(cfg, err):
    def check(name, v):
        if isinstance(v, dict):
            for k, x in v.items():
                check(f"{name}[{k!r}]", x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and not (0 <= v <= 1):
            err.append(f"{name} = {v}: должно быть от 0 до 1")

    for n in ("PREDICTIVE_MAINT_REDUCTION", "WAGON_DETECTION_PROB", "DETECTION_PROB",
              "CROSSING_REDUCTION", "PEOPLE_REDUCTION", "REGEN_USE_SHARE", "ECO_DRIVING_SAVING",
              "DEFECTIVE_WAGON_SHARE", "LOCO_WEAR", "ELECTRIC_EFFICIENCY", "DIESEL_EFFICIENCY",
              "ADVISORY_MAX_SLOWDOWN_SHARE"):
        check(n, getattr(cfg, n))
    if cfg.USE_BORDER:
        check("BORDER_PREANNOUNCE_PROB", cfg.BORDER_PREANNOUNCE_PROB)
    if cfg.USE_PORT:
        check("FERRY_DELAY_PROB", cfg.FERRY_DELAY_PROB)
        check("SLOT_DELAY_LEARNING", cfg.SLOT_DELAY_LEARNING)
    for k, p in cfg.HAZARDS.items():
        check(f"HAZARDS[{k!r}]['pass_incident_prob']", p["pass_incident_prob"])
