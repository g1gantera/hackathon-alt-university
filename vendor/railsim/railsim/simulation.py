"""Сборка модели, прогон сценариев и расчёт показателей (KPI)."""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpy

from .hazards import (apply_hazards, apply_incidents, apply_maintenance,
                      generate_raw_hazards, generate_raw_incidents)
from .line import FifoDispatcher, Segment, SmartDispatcher
from .terminals import Border, Port, generate_ferries
from .trains import generate_schedule, station_index, traffic_histogram, train_process


@dataclass
class Context:
    env: simpy.Environment
    cfg: object
    scenario: object
    seed: int
    segments: list
    border: object = None
    port: object = None
    border_idx: int = None
    port_idx: int = None
    stats: dict = field(default_factory=dict)


def _rngs(seed):
    """Отдельные потоки случайных чисел для разных типов событий."""
    return {k: np.random.default_rng([seed, i]) for i, k in
            enumerate(("schedule", "hazards", "incidents", "ferries"))}


def run_scenario(cfg, scenario, run_idx):
    seed = int(cfg.RANDOM_SEED) + run_idx
    rngs = _rngs(seed)
    horizon = float(cfg.SIM_DAYS) * 24.0
    end_time = horizon + float(cfg.DRAIN_HOURS)

    env = simpy.Environment()
    dispatcher = SmartDispatcher(cfg) if scenario.on("smart_dispatch") else FifoDispatcher()
    segments = [Segment(env, i, cfg.STATIONS[i], cfg.STATIONS[i + 1], spec, cfg, dispatcher)
                for i, spec in enumerate(cfg.SEGMENTS)]

    stats = {}
    stats.update(apply_hazards(segments, generate_raw_hazards(cfg, rngs["hazards"], horizon), cfg, scenario))
    stats.update(apply_incidents(segments, generate_raw_incidents(cfg, rngs["incidents"], horizon), cfg, scenario))
    apply_maintenance(segments, cfg, scenario, traffic_histogram(cfg, scenario))

    idx = station_index(cfg)
    ctx = Context(env=env, cfg=cfg, scenario=scenario, seed=seed, segments=segments, stats=stats)
    if cfg.USE_BORDER:
        ctx.border = Border(env, cfg, scenario, seed)
        ctx.border_idx = idx[cfg.BORDER_STATION]
    if cfg.USE_PORT:
        ctx.port = Port(env, cfg, scenario, generate_ferries(cfg, rngs["ferries"], end_time))
        ctx.port_idx = idx[cfg.PORT_STATION]

    trains = generate_schedule(cfg, rngs["schedule"])
    for tr in trains:
        env.process(train_process(env, tr, ctx))
    env.run(until=end_time)

    kpi = collect_kpis(cfg, trains, segments, stats, end_time)
    kpi.update({"scenario": scenario.name, "run": run_idx})
    return kpi, {"trains": trains, "segments": segments}


def _closure_hours(segments, until):
    total = 0.0
    for seg in segments:
        for s, e, _ in seg.closures:
            total += max(0.0, min(e, until) - max(s, 0.0))
    return total


def collect_kpis(cfg, trains, segments, stats, end_time):
    done = [t for t in trains if t.t_line_end is not None]
    # не доехавшие поезда тоже учитываем: задержка = сколько уже «лишнего» времени прошло
    stuck_delay = []
    for t in trains:
        if t.t_line_end is None:
            start = t.t_line_start if t.t_line_start is not None else t.sched_dep
            stuck_delay.append(max(0.0, end_time - start - t.min_line_time))
    delays = np.array([t.line_delay_h for t in done] + stuck_delay) if trains else np.zeros(1)

    def cost_h(t):
        return float(t.spec["delay_cost_kzt_h"])

    delay_cost = sum(t.line_delay_h * cost_h(t) for t in done)
    delay_cost += sum(d * cost_h(t) for d, t in zip(stuck_delay, [t for t in trains if t.t_line_end is None]))
    border_cost = sum(t.border_h * cost_h(t) for t in trains)
    port_trains = [t for t in trains if t.spec["to_port"] and t.t_line_end is not None]
    port_cost = sum((t.slot_hold_h + t.port_yard_queue_h + t.port_wait_h) * cost_h(t) for t in port_trains)

    electric = sum(t.electric_kwh for t in trains)
    diesel = sum(t.diesel_l for t in trains)
    energy_cost = electric * float(cfg.ELECTRICITY_PRICE_KZT_KWH) + diesel * float(cfg.DIESEL_PRICE_KZT_L)

    c = cfg.COSTS_KZT
    derail = sum(t.derailments for t in trains)
    loco = sum(t.loco_failures for t in trains)
    setouts = sum(t.wagon_setouts for t in trains)
    incident_cost = (derail * float(c["derailment"])
                     + stats["crossing_accidents"] * float(c["crossing_accident"])
                     + stats["people_incidents"] * float(c["people_incident"])
                     + loco * float(c["loco_failure"])
                     + setouts * float(c["wagon_setout"]))

    def mean(xs):
        return float(np.mean(xs)) if len(xs) else 0.0

    border_tr = [t for t in trains if t.border_h > 0]
    return {
        "trains_total": len(trains),
        "trains_completed": len(done),
        "avg_line_delay_h": float(np.mean(delays)),
        "p90_line_delay_h": float(np.percentile(delays, 90)),
        "total_line_delay_h": float(np.sum(delays)),
        "unplanned_stops": sum(t.unplanned_stops for t in trains),
        "smoothed_stops": sum(t.smoothed_stops for t in trains),
        "electric_kwh": electric,
        "diesel_l": diesel,
        "derailments": derail,
        "loco_failures": loco,
        "wagon_setouts": setouts,
        "crossing_accidents": stats["crossing_accidents"],
        "people_incidents": stats["people_incidents"],
        "hazards_total": stats["hazards_total"],
        "hazards_undetected": stats["hazards_undetected"],
        "closure_hours": _closure_hours(segments, end_time),
        "avg_border_h": mean([t.border_h for t in border_tr]),
        "avg_slot_hold_h": mean([t.slot_hold_h for t in port_trains]),
        "avg_port_yard_queue_h": mean([t.port_yard_queue_h for t in port_trains]),
        "avg_port_wait_h": mean([t.port_wait_h for t in port_trains]),
        "delay_cost_kzt": delay_cost,
        "border_cost_kzt": border_cost,
        "port_cost_kzt": port_cost,
        "energy_cost_kzt": energy_cost,
        "incident_cost_kzt": incident_cost,
        "total_cost_kzt": delay_cost + border_cost + port_cost + energy_cost + incident_cost,
    }


def run_monte_carlo(cfg, scenarios, progress=True):
    rows, details = [], {}
    n = int(cfg.N_RUNS)
    for r in range(n):
        for sc in scenarios:
            kpi, det = run_scenario(cfg, sc, r)
            rows.append(kpi)
            if r == 0:
                details[sc.name] = det
        if progress:
            print(f"  прогон {r + 1}/{n} готов", flush=True)
    return pd.DataFrame(rows), details
