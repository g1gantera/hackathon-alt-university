"""Поезда: генерация графика отправлений и процесс движения по линии."""
import math
from dataclasses import dataclass, field

import numpy as np

from . import energy as en
from .util import EPS, crn_uniform, pick


@dataclass(eq=False)
class Train:
    id: int
    type_name: str
    spec: dict
    origin: int
    dest: int
    sched_dep: float
    u_loco: np.ndarray
    u_wagon: np.ndarray
    u_wagon_det: np.ndarray
    direction: int = 1
    min_line_time: float = 0.0
    # результаты
    t_line_start: float = None
    t_line_end: float = None
    line_delay_h: float = 0.0
    delay_so_far: float = 0.0
    border_h: float = 0.0
    slot_hold_h: float = 0.0
    port_yard_queue_h: float = 0.0
    port_wait_h: float = 0.0
    planned_stops: int = 0
    unplanned_stops: int = 0
    smoothed_stops: int = 0
    electric_kwh: float = 0.0
    diesel_l: float = 0.0
    derailments: int = 0
    loco_failures: int = 0
    wagon_setouts: int = 0
    finished: bool = False
    booked_ferry: int = None
    ferry_index: int = None
    trace: list = field(default_factory=list)   # (время, км) для графика движения

    @property
    def priority(self):
        return float(self.spec["priority"])

    @property
    def mass_t(self):
        return float(self.spec["mass_t"])

    @property
    def vmax(self):
        return float(self.spec["max_speed_kmh"])

    def path(self):
        """Список (индекс перегона, станция входа, станция выхода) в порядке движения."""
        if self.direction == 1:
            return [(i, i, i + 1) for i in range(self.origin, self.dest)]
        return [(i - 1, i, i - 1) for i in range(self.origin, self.dest, -1)]


def station_index(cfg):
    return {s["name"]: i for i, s in enumerate(cfg.STATIONS)}


def segment_min_time(cfg, seg_idx, vmax):
    spec = cfg.SEGMENTS[seg_idx]
    length = abs(float(cfg.STATIONS[seg_idx + 1]["km"]) - float(cfg.STATIONS[seg_idx]["km"]))
    return length / min(vmax, float(spec["max_speed_kmh"]))


def min_line_time(cfg, tr):
    """Время хода без ожиданий и ограничений + плановые стоянки (без границы и порта)."""
    total = 0.0
    for k, (si, a, _) in enumerate(tr.path()):
        if k > 0 and tr.spec["passenger"] and cfg.STATIONS[a]["passenger_stop"]:
            total += float(cfg.PASSENGER_DWELL_H)
        total += segment_min_time(cfg, si, tr.vmax)
    return total


def _hour_weights(flow):
    w = flow.get("departure_hour_weights")
    if not w:
        return np.full(24, 1 / 24)
    w = np.asarray(w, dtype=float)
    return w / w.sum()


def generate_schedule(cfg, rng):
    """Отправления на весь период. Один и тот же rng → одинаковый график в обоих сценариях."""
    idx = station_index(cfg)
    trains, tid = [], 0
    for day in range(int(cfg.SIM_DAYS)):
        for flow in cfg.TRAIN_FLOWS:
            if flow.get("fixed_departure_hours"):
                hours = [float(h) for h in flow["fixed_departure_hours"]]
            else:
                n = rng.poisson(float(flow["trains_per_day"]))
                hours = list(rng.choice(24, size=n, p=_hour_weights(flow)) + rng.random(n))
            o, d = idx[flow["origin"]], idx[flow["destination"]]
            nseg = abs(d - o)
            for h in sorted(hours):
                tr = Train(
                    id=tid, type_name=flow["type"], spec=cfg.TRAIN_TYPES[flow["type"]],
                    origin=o, dest=d, sched_dep=day * 24.0 + h,
                    u_loco=rng.random(nseg), u_wagon=rng.random(nseg), u_wagon_det=rng.random(nseg),
                    direction=1 if d > o else -1,
                )
                tr.min_line_time = min_line_time(cfg, tr)
                trains.append(tr)
                tid += 1
    return trains


def traffic_histogram(cfg, scenario=None):
    """
    Ожидаемая «стоимость движения» по часам суток на каждом перегоне:
    число поездов × стоимость часа их задержки. По ней автодиспетчер выбирает «окна».
    """
    idx = station_index(cfg)
    hist = [np.zeros(24) for _ in cfg.SEGMENTS]
    for flow in cfg.TRAIN_FLOWS:
        spec = cfg.TRAIN_TYPES[flow["type"]]
        o, d = idx[flow["origin"]], idx[flow["destination"]]
        probe = Train(0, flow["type"], spec, o, d, 0.0, None, None, None, direction=1 if d > o else -1)
        cost = float(spec["delay_cost_kzt_h"])
        if flow.get("fixed_departure_hours"):
            deps = [(float(h), cost) for h in flow["fixed_departure_hours"]]
        else:
            w = _hour_weights(flow) * float(flow["trains_per_day"]) * cost
            deps = [(h + 0.5, w[h]) for h in range(24)]
        offset = 0.0
        # поезд сначала проходит погранпереход на станции отправления
        if cfg.USE_BORDER and spec["cross_border"] and flow["origin"] == cfg.BORDER_STATION and scenario:
            pre = float(pick(cfg.BORDER_PREANNOUNCE_PROB, scenario, "border_preannounce"))
            offset += (float(cfg.BORDER_GAUGE_CHANGE_H)
                       + float(pick(cfg.BORDER_CUSTOMS_H, scenario, "border_preannounce"))
                       + (1 - pre) * float(cfg.BORDER_PREP_H))
        for si, _, _ in probe.path():
            for h, weight in deps:
                hist[si][int(h + offset) % 24] += weight
            offset += segment_min_time(cfg, si, probe.vmax)
    return hist


def _add_energy(tr, ctx, mech_kwh, electrified, braking=False):
    regen = float(pick(ctx.cfg.REGEN_USE_SHARE, ctx.scenario, "energy_optimization")) if braking else 0.0
    e, d = en.to_supply(ctx.cfg, mech_kwh, electrified, regen)
    tr.electric_kwh += e
    tr.diesel_l += d


def train_process(env, tr, ctx):
    cfg, sc = ctx.cfg, ctx.scenario
    if tr.sched_dep > env.now:
        yield env.timeout(tr.sched_dep - env.now)

    to_port = ctx.port is not None and tr.spec["to_port"] and tr.dest == ctx.port_idx
    path = tr.path()
    border_on_path = (ctx.border is not None and tr.spec["cross_border"]
                      and ctx.border_idx in [a for _, a, _ in path] + [tr.dest])

    # Бронирование слота на паром: поезд придерживается на станции отправления,
    # чтобы не стоять в порту и на подходах к нему.
    if to_port and sc.on("port_slots"):
        eta = env.now + tr.min_line_time * (1 + float(cfg.SLOT_ETA_MARGIN))
        if border_on_path:
            eta += ctx.border.expected_total_h()
        hold = ctx.port.book(tr, eta)
        if hold > 0:
            tr.slot_hold_h = hold
            yield env.timeout(hold)

    tr.t_line_start = env.now
    excluded = 0.0
    tr.trace.append((env.now, float(cfg.STATIONS[tr.origin]["km"])))

    eco = float(pick(cfg.ECO_DRIVING_SAVING, sc, "energy_optimization"))
    loco_red = float(pick(cfg.PREDICTIVE_MAINT_REDUCTION, sc, "predictive_maintenance"))
    wagon_det = float(pick(cfg.WAGON_DETECTION_PROB, sc, "wagon_detectors"))
    loco_rate = float(cfg.LOCO_FAILURE_BASE_PER_1000KM) * (
        1 + float(cfg.LOCO_WEAR_SENSITIVITY) * float(cfg.LOCO_WEAR)) * (1 - loco_red)
    n_def_wagons = float(tr.spec["wagons"]) * float(cfg.DEFECTIVE_WAGON_SHARE)

    prev_tt = None
    for k, (si, a, b) in enumerate(path):
        seg = ctx.segments[si]
        km_a = float(cfg.STATIONS[a]["km"])
        km_b = float(cfg.STATIONS[b]["km"])
        v_line = min(tr.vmax, seg.max_speed)
        planned = False

        if k > 0:
            # погранпереход на промежуточной станции
            if border_on_path and a == ctx.border_idx:
                t0 = env.now
                yield from ctx.border.process(tr)
                excluded += env.now - t0
                planned = True
            # плановая стоянка пассажирского поезда
            if tr.spec["passenger"] and cfg.STATIONS[a]["passenger_stop"]:
                planned = True
                tr.planned_stops += 1
                yield env.timeout(float(cfg.PASSENGER_DWELL_H))
            if planned:
                _add_energy(tr, ctx, en.stop_kwh(tr.mass_t, v_line), seg.electrified, braking=True)
        elif border_on_path and tr.origin == ctx.border_idx:
            t0 = env.now
            yield from ctx.border.process(tr)
            excluded += env.now - t0

        # запрос перегона у диспетчера
        t_req = env.now
        yield seg.request(tr, tr.direction)
        wait = env.now - t_req
        tr.delay_so_far += wait
        if k > 0 and wait > EPS and not planned:
            can_absorb = (sc.on("speed_advisory") and prev_tt is not None
                          and wait <= float(cfg.ADVISORY_MAX_SLOWDOWN_SHARE) * prev_tt)
            if can_absorb:
                tr.smoothed_stops += 1       # подъехал медленнее и прошёл без остановки
            else:
                tr.unplanned_stops += 1      # остановка «на красный»: потеря энергии
                _add_energy(tr, ctx, en.stop_kwh(tr.mass_t, v_line), seg.electrified, braking=True)
        if env.now - tr.trace[-1][0] > EPS:
            tr.trace.append((env.now, km_a))

        # проследование перегона
        t_in = env.now
        v = v_line * seg.speed_factor(t_in)
        tt = seg.length_km / v
        extra = 0.0
        derailed = False

        # необнаруженные угрозы (лопнувший рельс, украденные болты, размыв, выброс пути)
        for hs, he, pp, kind, hid in seg.hazard_windows:
            if hs <= t_in < he and crn_uniform(ctx.seed, "pass", tr.id, hid) < pp:
                derailed = True
                break
        # отказ локомотива
        p_loco = 1 - math.exp(-loco_rate * seg.length_km / 1000.0)
        if tr.u_loco[k] < p_loco:
            tr.loco_failures += 1
            extra += float(cfg.LOCO_RESCUE_H)
        # дефект вагона: пойман датчиком → отцепка, не пойман → сход
        setout = False
        p_wagon = 1 - math.exp(-float(cfg.WAGON_DEFECT_RATE_PER_1000KM) * n_def_wagons * seg.length_km / 1000.0)
        if tr.u_wagon[k] < p_wagon:
            if tr.u_wagon_det[k] < wagon_det:
                setout = True
            else:
                derailed = True
        if derailed:
            tr.derailments += 1
            closure = float(cfg.DERAIL_CLOSURE_H)
            seg.add_closure(env.now, env.now + closure, "derailment")
            extra += closure

        grade = seg.grade * tr.direction
        mech = en.traction_kwh(cfg, tr.mass_t, v, seg.length_km, grade) * (1 - eco)
        _add_energy(tr, ctx, mech, seg.electrified)

        yield env.timeout(tt + extra)
        seg.release(tr.direction)
        tr.trace.append((env.now, km_b))
        prev_tt = tt

        if setout:
            tr.wagon_setouts += 1
            tr.unplanned_stops += 1
            _add_energy(tr, ctx, en.stop_kwh(tr.mass_t, v_line), seg.electrified, braking=True)
            yield env.timeout(float(cfg.WAGON_SETOUT_H))
            tr.trace.append((env.now, km_b))

    if border_on_path and tr.dest == ctx.border_idx:
        t0 = env.now
        yield from ctx.border.process(tr)
        excluded += env.now - t0

    tr.t_line_end = env.now
    tr.line_delay_h = max(0.0, (tr.t_line_end - tr.t_line_start) - excluded - tr.min_line_time)

    if to_port:
        ctx.port.record_line_delay(tr.line_delay_h)
        yield from ctx.port.arrive(tr)
    tr.finished = True
