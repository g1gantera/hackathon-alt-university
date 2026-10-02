"""
Генерация случайных событий на линии и их применение к сценарию.

Сначала события генерируются ОДИН раз на прогон (не зависят от сценария):
угрозы на пути, ДТП на переездах, происшествия с людьми. Затем каждый
сценарий по-своему на них реагирует: раньше или позже обнаруживает,
предотвращает или нет.
"""
from dataclasses import dataclass

import numpy as np

from .util import pick

WEATHER_KINDS = ("flood", "snow", "sand", "heat")
DEFECT_KINDS = ("rail_defect", "theft")
HAZARD_KINDS = DEFECT_KINDS + WEATHER_KINDS
HAZARD_RISK_KEY = {
    "rail_defect": "wear", "theft": "theft_risk", "flood": "flood_risk",
    "snow": "snow_risk", "sand": "sand_risk", "heat": "heat_risk",
}


@dataclass
class RawHazard:
    hid: int
    kind: str
    seg_idx: int
    start: float
    duration: float
    u_detect: float


@dataclass
class RawIncident:
    kind: str          # "crossing" | "people"
    seg_idx: int
    time: float
    u_keep: float


def _seg_length(cfg, i):
    return abs(float(cfg.STATIONS[i + 1]["km"]) - float(cfg.STATIONS[i]["km"]))


def generate_raw_hazards(cfg, rng, horizon_h):
    out, hid = [], 0
    for si, spec in enumerate(cfg.SEGMENTS):
        length = _seg_length(cfg, si)
        for kind in HAZARD_KINDS:
            p = cfg.HAZARDS[kind]
            mult = float(spec[HAZARD_RISK_KEY[kind]])
            rate_day = float(p["rate_per_100km_day"]) * length / 100.0 * mult
            n = rng.poisson(rate_day * horizon_h / 24.0)
            for _ in range(n):
                out.append(RawHazard(
                    hid=hid, kind=kind, seg_idx=si,
                    start=float(rng.uniform(0, horizon_h)),
                    duration=float(rng.exponential(float(p["mean_duration_h"]))),
                    u_detect=float(rng.random()),
                ))
                hid += 1
    return out


def generate_raw_incidents(cfg, rng, horizon_h):
    out = []
    years = horizon_h / (365.0 * 24.0)
    rates = cfg.CROSSING_ACCIDENT_RATE_PER_YEAR
    for si, spec in enumerate(cfg.SEGMENTS):
        length = _seg_length(cfg, si)
        cross_rate = (int(spec["crossings_guarded"]) * float(rates["guarded"])
                      + int(spec["crossings_unguarded"]) * float(rates["unguarded"]))
        people_rate = float(cfg.PEOPLE_INCIDENT_RATE_PER_100KM_YEAR) * length / 100.0
        for kind, rate in (("crossing", cross_rate), ("people", people_rate)):
            for _ in range(rng.poisson(rate * years)):
                out.append(RawIncident(kind, si, float(rng.uniform(0, horizon_h)), float(rng.random())))
    return out


def apply_hazards(segments, raw, cfg, scenario):
    """Превращает сырые угрозы в закрытия/ограничения и окна риска для сценария."""
    det = pick(cfg.DETECTION_PROB, scenario, "hazard_monitoring")
    stats = {"hazards_total": 0, "hazards_undetected": 0}
    for h in raw:
        p = cfg.HAZARDS[h.kind]
        seg = segments[h.seg_idx]
        detected = h.u_detect < float(det[h.kind])
        discover = h.start if detected else h.start + float(p["undetected_discovery_h"])
        if h.kind in WEATHER_KINDS:
            end = h.start + h.duration               # явление закончится само
        else:
            end = discover + h.duration              # ремонт после обнаружения
        if discover < end:
            if p["closure"]:
                seg.add_closure(discover, end, h.kind)
            else:
                seg.add_restriction(discover, end, float(p["speed_factor"]), h.kind)
        risk_end = min(discover, end)
        if risk_end > h.start and float(p["pass_incident_prob"]) > 0:
            seg.hazard_windows.append((h.start, risk_end, float(p["pass_incident_prob"]), h.kind, h.hid))
        stats["hazards_total"] += 1
        stats["hazards_undetected"] += int(not detected)
    return stats


def apply_incidents(segments, raw, cfg, scenario):
    """ДТП на переездах и происшествия с людьми: часть предотвращается в auto."""
    red_cross = float(pick(cfg.CROSSING_REDUCTION, scenario, "crossing_safety"))
    red_people = float(pick(cfg.PEOPLE_REDUCTION, scenario, "crossing_safety"))
    stats = {"crossing_accidents": 0, "people_incidents": 0}
    for inc in raw:
        if inc.kind == "crossing":
            if inc.u_keep < 1.0 - red_cross:
                segments[inc.seg_idx].add_closure(inc.time, inc.time + float(cfg.CROSSING_CLOSURE_H), "crossing")
                stats["crossing_accidents"] += 1
        else:
            if inc.u_keep < 1.0 - red_people:
                segments[inc.seg_idx].add_closure(inc.time, inc.time + float(cfg.PEOPLE_CLOSURE_H), "people")
                stats["people_incidents"] += 1
    return stats


def apply_maintenance(segments, cfg, scenario, traffic_hist):
    """
    «Окна» на ремонт пути. Базовый сценарий — фиксированный час начала.
    Автодиспетчер выбирает час с минимальным ожидаемым движением по перегону.
    """
    interval = int(cfg.MAINT_INTERVAL_DAYS)
    if interval <= 0:
        return
    dur = float(cfg.MAINT_DURATION_H)
    width = max(1, int(np.ceil(dur)))
    for seg in segments:
        if scenario.on("maintenance_windows"):
            hist = traffic_hist[seg.idx]
            load = [sum(hist[(h + k) % 24] for k in range(width)) for h in range(24)]
            start_hour = int(np.argmin(load))
        else:
            start_hour = float(cfg.MAINT_BASELINE_START_HOUR)
        for day in range(seg.idx % interval, int(cfg.SIM_DAYS), interval):
            start = day * 24.0 + start_hour
            seg.add_closure(start, start + dur, "maintenance")
