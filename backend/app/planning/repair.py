"""Conservative constructive recovery preserving every entered movement.

Admitted trains wait at their pinned boundary station. Future trains are booked
around all committed resources. No movement or past arrival is rewritten.
"""

import time
import uuid

from ..schemas import Block, Plan
from ..validation.plan import validate_plan
from .baseline import build_baseline
from .common import clearance_s


def repair_plan(scenario, previous, time_budget_s=0.5):
    began = time.perf_counter()
    now = scenario.now_s
    sections = {s.id: s for s in scenario.sections}
    stations = {s.id: s for s in scenario.stations}
    stops = {(s.train_id, s.station_id): s for s in previous.stops}
    busy = []
    parts = {}
    all_stops = []
    all_moves = []
    for train in scenario.trains:
        locked = sorted(
            (m for m in previous.movements if m.train_id == train.id and m.start_s <= now),
            key=lambda m: train.route.index(m.origin),
        )
        boundary = len(locked)
        boundary_stop = stops[train.id, train.route[boundary]]
        fixed = bool(locked) or boundary_stop.arrival_s <= now
        parts[train.id] = (boundary, fixed, boundary_stop)
        all_moves.extend(locked)
        for i, sid in enumerate(train.route):
            old = stops[train.id, sid]
            station = stations[sid]
            if i < boundary or boundary == len(train.route) - 1:
                all_stops.append(old)
                busy.append(
                    (
                        train.id,
                        "stop",
                        f"track:{sid}:{old.track_id}",
                        old.arrival_s,
                        old.departure_s + station.clearance_s,
                    )
                )
                if station.switch:
                    if i > 0:
                        busy.append(
                            (
                                train.id,
                                "switch",
                                f"switch:{sid}:{station.switch.id}",
                                old.arrival_s,
                                old.arrival_s + station.switch.clearance_s,
                            )
                        )
                    if i < len(train.route) - 1:
                        busy.append(
                            (
                                train.id,
                                "switch",
                                f"switch:{sid}:{station.switch.id}",
                                old.departure_s,
                                old.departure_s + station.switch.clearance_s,
                            )
                        )
            elif i == boundary and fixed:
                busy.append(
                    (
                        train.id,
                        "stop",
                        f"track:{sid}:{old.track_id}",
                        old.arrival_s,
                        max(now + 1, old.arrival_s + train.min_dwell_s) + station.clearance_s,
                    )
                )
                if i > 0 and station.switch:
                    busy.append(
                        (
                            train.id,
                            "switch",
                            f"switch:{sid}:{station.switch.id}",
                            old.arrival_s,
                            old.arrival_s + station.switch.clearance_s,
                        )
                    )
        for m in locked:
            section = sections[m.section_id]
            end = m.end_s + clearance_s(train, section)
            for r in [f"main_track:{section.id}:{m.main_track_id}", *section.shared_resources]:
                busy.append((train.id, "move", r, m.start_s, end))
            if section.id in train.section_recovery_all_tracks:
                busy.append(
                    (train.id, "move", f"section:{section.id}", m.start_s, m.start_s + m.hold_s)
                )
    ordered = sorted(
        scenario.trains, key=lambda t: (not parts[t.id][1], parts[t.id][2].arrival_s, -t.priority)
    )
    future = []
    for train in ordered:
        boundary, fixed, old = parts[train.id]
        if boundary == len(train.route) - 1:
            continue
        remaining = time_budget_s - (time.perf_counter() - began)
        if remaining <= 0.005:
            return None
        t = train.model_copy(deep=True)
        t.route = t.route[boundary:]
        t.release_s = old.arrival_s if fixed else max(now + 1, t.release_s)
        t.due_s = max(t.due_s, t.release_s)
        if not fixed:
            future.append(t)
            continue
        t.not_before_s = {k: v for k, v in t.not_before_s.items() if k in t.route}
        t.not_before_s[t.route[0]] = max(now + 1, t.not_before_s.get(t.route[0], 0))
        t.manual_station_tracks = {k: v for k, v in t.manual_station_tracks.items() if k in t.route}
        if fixed:
            t.manual_station_tracks[t.route[0]] = old.track_id
        route_sections = {
            s.id
            for s in scenario.sections
            if any({s.station_a, s.station_b} == {a, b} for a, b in zip(t.route, t.route[1:]))
        }
        t.manual_main_tracks = {
            k: v for k, v in t.manual_main_tracks.items() if k in route_sections
        }
        t.section_hold_s = {k: v for k, v in t.section_hold_s.items() if k in route_sections}
        t.section_recovery_all_tracks = [
            s for s in t.section_recovery_all_tracks if s in route_sections
        ]
        sub = scenario.model_copy(
            update={"now_s": 0, "trains": [t], "blocks": list(scenario.blocks)}
        )
        sub.blocks.extend(
            Block(id=f"repair-{i}", resource=r, start_s=a, end_s=b)
            for i, (tid, kind, r, a, b) in enumerate(busy)
            if not (tid == t.id and kind == "stop")
        )
        result = build_baseline(
            sub, time_budget_s=max(0.005, remaining), fixed_admissions={t.id} if fixed else None
        )
        if result.plan is None:
            return None
        all_stops.extend(result.plan.stops)
        all_moves.extend(result.plan.movements)
        for stop in result.plan.stops:
            station = stations[stop.station_id]
            busy.append(
                (
                    t.id,
                    "stop",
                    f"track:{station.id}:{stop.track_id}",
                    stop.arrival_s,
                    stop.departure_s + station.clearance_s,
                )
            )
            if station.switch:
                i = t.route.index(station.id)
                for event in ([stop.arrival_s] if i > 0 else []) + (
                    [stop.departure_s] if i < len(t.route) - 1 else []
                ):
                    busy.append(
                        (
                            t.id,
                            "switch",
                            f"switch:{station.id}:{station.switch.id}",
                            event,
                            event + station.switch.clearance_s,
                        )
                    )
        for m in result.plan.movements:
            section = sections[m.section_id]
            end = m.end_s + clearance_s(t, section)
            for r in [f"main_track:{section.id}:{m.main_track_id}", *section.shared_resources]:
                busy.append((t.id, "move", r, m.start_s, end))
    if future:
        remaining = time_budget_s - (time.perf_counter() - began)
        if remaining <= 0.005:
            return None
        sub = scenario.model_copy(
            update={"now_s": 0, "trains": future, "blocks": list(scenario.blocks)}
        )
        sub.blocks.extend(
            Block(id=f"repair-batch-{i}", resource=r, start_s=a, end_s=b)
            for i, (_, _, r, a, b) in enumerate(busy)
        )
        result = build_baseline(sub, time_budget_s=remaining)
        if result.plan is None:
            return None
        all_stops.extend(result.plan.stops)
        all_moves.extend(result.plan.movements)
    plan = Plan(
        id="repair-" + uuid.uuid4().hex[:10],
        scenario_id=scenario.id,
        state_version=scenario.state_version,
        strategy="balanced",
        solver_status="FEASIBLE",
        stops=all_stops,
        movements=all_moves,
        explanations=[
            "Конструктивное восстановление: начатые движения сохранены; ожидание на закреплённой станции; новые рейсы размещены вокруг занятых ресурсов. Оптимальность не доказана."
        ],
    )
    return None if validate_plan(scenario, plan, previous) else plan
