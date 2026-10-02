"""Deterministic FCFS whole-route reservation heuristic.

Trains queue outside the modeled network until their complete route can be
reserved. Routes can overlap in time on different resources. No backtracking
or reordering of previously booked trains. Failure is UNKNOWN, not a proof
that the scenario is infeasible.
"""

import math
import time
import uuid

from backend.app.advisory.speed import minimum_duration_s, section_at_entry
from backend.app.planning.common import allowed_main_tracks, clearance_s, merged_closures, section_for, wear_cost
from backend.app.schemas import Movement, Plan, PlanResult, Scenario, Stop
from backend.app.validation.plan import validate_plan


def build_baseline(
    scenario: Scenario, time_budget_s: float = 1.0, *, duration_multiplier: float = 1.0, fixed_admissions: set[str] | None = None
) -> PlanResult:
    started = time.perf_counter()

    def result(status, plan=None, message=None):
        elapsed = (time.perf_counter() - started) * 1000
        if plan:
            plan.elapsed_ms = elapsed
        return PlanResult(
            status=status,
            plan=plan,
            elapsed_ms=elapsed,
            diagnostics=[] if message is None else [message],
        )

    if not math.isfinite(time_budget_s) or time_budget_s <= 0:
        raise ValueError("Budget must be finite and positive")
    if not math.isfinite(duration_multiplier) or duration_multiplier < 1:
        raise ValueError("Duration multiplier must be finite and >= 1")
    if scenario.now_s > 0:
        return result(
            "INVALID", message="FCFS baseline is for initial planning; use solve_plan to replan"
        )
    fixed_admissions = set(fixed_admissions or ()) | set(scenario.metadata.get("execution_origin_arrivals", {}))
    station_map = {s.id: s for s in scenario.stations}
    occupied = [
        (resource, start, end)
        for resource, intervals in merged_closures(scenario).items()
        for start, end in intervals
    ]
    all_stops, all_moves = [], []
    for train in sorted(scenario.trains, key=lambda t: (t.release_s, -t.priority, t.id)):
        admission = train.release_s
        initial_wait = 0
        while admission < scenario.horizon_s:
            if time.perf_counter() - started >= time_budget_s:
                return result("UNKNOWN", message="FCFS time budget exhausted")
            stops, moves, pending = [], [], []
            shift, arrival = 0, admission
            for i, station_id in enumerate(train.route):
                station = station_map[station_id]
                departure = max(arrival + train.min_dwell_s, train.not_before_s.get(station_id, 0))
                if i==0:departure+=initial_wait
                if i < len(train.route) - 1:
                    section = section_for(scenario, station_id, train.route[i + 1])
                    # Clear this train's previous traversal of a shared junction
                    # by waiting here. Shifting admission cannot resolve a self-conflict.
                    own_releases = [end for r, _, end in pending if r in section.shared_resources]
                    departure = max([departure, *own_releases])
                if i == len(train.route) - 1 and departure != arrival + train.min_dwell_s:
                    shift = max(shift, departure - arrival - train.min_dwell_s)
                if station.switch and 0 < i < len(train.route) - 1:
                    departure = max(departure, arrival + station.switch.clearance_s)
                if station.switch:
                    resource = f"switch:{station_id}:{station.switch.id}"
                    events = ([arrival] if i > 0 else []) + (
                        [departure] if i < len(train.route) - 1 else [])
                    for event in events:
                        end = event + station.switch.clearance_s
                        for other, a, b in occupied + pending:
                            if resource == other and event < b and a < end:
                                shift = max(shift, b - event)
                        pending.append((resource, event, end))
                reservation_start = moves[-1].start_s if i > 0 and scenario.metadata.get("reserve_receiving_tracks") else arrival
                options = []
                for track in station.tracks:
                    if station_id in train.manual_station_tracks and train.manual_station_tracks[station_id] != track.id:
                        continue
                    if track.length_m < train.length_m:
                        continue
                    resource = f"track:{station_id}:{track.id}"
                    conflicts = [
                        end - reservation_start
                        for r, start, end in occupied + pending
                        if r == resource
                        and reservation_start < end
                        and start < departure + station.clearance_s
                    ]
                    options.append((max(conflicts, default=0), wear_cost(scenario, resource), track.id, resource))
                track_shift, _, track_id, resource = min(options)
                shift = max(shift, track_shift)
                stops.append(
                    Stop(
                        train_id=train.id,
                        station_id=station_id,
                        track_id=track_id,
                        arrival_s=arrival,
                        departure_s=departure,
                    )
                )
                pending.append((resource, reservation_start, departure + station.clearance_s))
                if i == len(train.route) - 1:
                    continue
                destination = train.route[i + 1]
                section = section_for(scenario, station_id, destination)
                end = (
                    departure
                    + math.ceil(
                        minimum_duration_s(
                            train, section_at_entry(train, section, departure), station_id
                        )
                        * duration_multiplier
                    )
                    + train.section_hold_s.get(section.id, 0)
                )
                release = end + clearance_s(train, section)
                main_options = []
                for main_track in allowed_main_tracks(section, station_id, train):
                    resource = f"main_track:{section.id}:{main_track.id}"
                    conflicts = [
                        b - departure
                        for r, a, b in occupied + pending
                        if r == resource and departure < b and a < release
                    ]
                    conflicts.extend(
                        block.end_s - departure
                        for block in scenario.blocks
                        if block.kind == "signal"
                        and block.resource in (resource, f"section:{section.id}")
                        and block.start_s <= departure < block.end_s
                    )
                    main_options.append((max(conflicts, default=0), wear_cost(scenario, resource), main_track.id, resource))
                if not main_options:
                    return result(
                        "UNKNOWN", message=f"No main track permits {station_id} → {destination}"
                    )
                main_shift, _, main_track_id, resource = min(main_options)
                shift = max(shift, main_shift)
                if section.id in train.section_recovery_all_tracks:
                    recovery_end = departure + train.section_hold_s[section.id]
                    for track in section.main_tracks:
                        other_resource = f"main_track:{section.id}:{track.id}"
                        for r, a, b in occupied + pending:
                            if r == other_resource and departure < b and a < recovery_end:
                                shift = max(shift, b - departure)
                        if track.id != main_track_id:
                            pending.append((other_resource, departure, recovery_end))
                for r in [resource, *section.shared_resources]:
                    for other, a, b in occupied + pending:
                        if r == other and departure < b and a < release:
                            shift = max(shift, b - departure)
                    pending.append((r, departure, release))
                moves.append(
                    Movement(
                        train_id=train.id,
                        section_id=section.id,
                        main_track_id=main_track_id,
                        origin=station_id,
                        destination=destination,
                        start_s=departure,
                        end_s=end,
                        hold_s=train.section_hold_s.get(section.id, 0),
                    )
                )
                arrival = end
            if max(end for _, _, end in pending) > scenario.horizon_s:
                return result("UNKNOWN", message="FCFS cannot fit trains within horizon")
            if shift:
                if fixed_admissions and train.id in fixed_admissions:
                    initial_wait += max(1,shift)
                else:
                    admission += max(1, shift)
                continue
            all_stops.extend(stops)
            all_moves.extend(moves)
            occupied.extend(pending)
            break
        else:
            return result("UNKNOWN", message="FCFS could not reserve a route")
    plan = Plan(
        id=f"baseline-{uuid.uuid4().hex[:10]}",
        scenario_id=scenario.id,
        state_version=scenario.state_version,
        strategy="baseline",
        solver_status="FEASIBLE",
        stops=all_stops,
        movements=all_moves,
        explanations=[
            (
                "FCFS: резервирование полного маршрута в порядке готовности поездов; "
                "ожидание допуска происходит вне моделируемого участка."
            )
        ],
    )
    errors = validate_plan(scenario, plan)
    if errors:
        return result("INVALID", message="; ".join(v.message for v in errors))
    return result("FEASIBLE", plan)
