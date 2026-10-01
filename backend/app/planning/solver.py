"""CP-SAT scheduling with distinct station and main-line track resources."""

import math
import time
import uuid
from collections import defaultdict

from ortools.sat.python import cp_model

from backend.app.advisory.speed import duration_windows
from backend.app.planning.baseline import build_baseline
from backend.app.planning.common import (
    allowed_main_tracks,
    clearance_s,
    committed_fields,
    merged_closures,
    section_for,
)
from backend.app.schemas import Movement, Plan, PlanResult, Scenario, Stop
from backend.app.validation.plan import validate_plan


def solve_plan(
    scenario: Scenario,
    *,
    strategy: str = "balanced",
    time_budget_s: float = 2.0,
    previous: Plan | None = None,
) -> PlanResult:
    """Synchronous CPU function. Backend should run it in a worker process.

    The budget includes model building and leaves a small reserve for validation;
    it is a soft wall-clock target, not a hard real-time guarantee.
    """
    started = time.perf_counter()

    def result(status, plan=None, diagnostics=()):
        elapsed = (time.perf_counter() - started) * 1000
        if plan is not None:
            plan.elapsed_ms = elapsed
        return PlanResult(
            status=status, plan=plan, elapsed_ms=elapsed, diagnostics=list(diagnostics)
        )

    if strategy not in ("balanced", "passenger", "eco"):
        raise ValueError("Unknown strategy")
    if not math.isfinite(time_budget_s) or time_budget_s <= 0:
        raise ValueError("time_budget_s must be finite and positive")
    if scenario.now_s > 0 and previous is None:
        return result("INVALID", diagnostics=["Replanning requires previous plan"])
    if previous and (
        previous.scenario_id != scenario.id or previous.state_version > scenario.state_version
    ):
        return result("INVALID", diagnostics=["Previous plan belongs to another state/scenario"])
    if previous:
        expected = {(t.id, s) for t in scenario.trains for s in t.route}
        expected_moves = {(t.id, s) for t in scenario.trains for s in t.route[:-1]}
        if (
            {(s.train_id, s.station_id) for s in previous.stops} != expected
            or len(previous.stops) != len(expected)
            or {(m.train_id, m.origin) for m in previous.movements} != expected_moves
            or len(previous.movements) != len(expected_moves)
        ):
            return result("INVALID", diagnostics=["Previous plan does not cover the scenario"])
    model = cp_model.CpModel()
    # Seed CP-SAT with a checked constructive schedule; do not spend the whole
    # short budget searching for the first feasible assignment from scratch.
    seed = previous
    if scenario.now_s == 0 and time_budget_s > 0.05:
        seed = build_baseline(
            scenario,
            time_budget_s=min(0.3, time_budget_s * 0.15),
            duration_multiplier=1.15 if strategy == "eco" else 1.0,
        ).plan
    seed_stops = {} if seed is None else {(s.train_id, s.station_id): s for s in seed.stops}
    seed_moves = {} if seed is None else {(m.train_id, m.origin): m for m in seed.movements}
    resources = defaultdict(list)
    section_intervals, recoveries = defaultdict(list), []
    arrivals, departures, tracks, locked_moves = committed_fields(scenario, previous)
    station_map = {s.id: s for s in scenario.stations}
    stop_vars, move_vars = {}, []
    cost_terms = []
    previous_stops = (
        {} if previous is None else {(s.train_id, s.station_id): s for s in previous.stops}
    )
    for train in scenario.trains:
        for index, station_id in enumerate(train.route):
            key = (train.id, station_id)
            station = station_map[station_id]
            arrival = model.new_int_var(0, scenario.horizon_s, f"arrival_{key}")
            departure = model.new_int_var(0, scenario.horizon_s, f"departure_{key}")
            model.add(departure >= arrival + train.min_dwell_s)
            model.add(departure >= train.not_before_s.get(station_id, 0))
            if key in arrivals:
                model.add(arrival == arrivals[key])
            else:
                model.add(arrival >= scenario.now_s)
            if key in departures:
                model.add(departure == departures[key])
            else:
                model.add(departure >= scenario.now_s)
            if index == 0:
                model.add(arrival >= train.release_s)
            if index == len(train.route) - 1:
                # The train leaves the model after terminal dwell.
                model.add(departure == arrival + train.min_dwell_s)
            size = model.new_int_var(1, scenario.horizon_s, f"track_duration_{key}")
            end = model.new_int_var(0, scenario.horizon_s, f"track_end_{key}")
            model.add(end == departure + station.clearance_s)
            model.add(size == end - arrival)
            options = {}
            for track in station.tracks:
                if track.length_m < train.length_m:
                    continue
                chosen = model.new_bool_var(f"track_{key}_{track.id}")
                options[track.id] = chosen
                interval = model.new_optional_interval_var(
                    arrival, size, end, chosen, f"occupancy_{key}_{track.id}"
                )
                resources[f"track:{station_id}:{track.id}"].append(interval)
                if key in tracks:
                    model.add(chosen == int(track.id == tracks[key]))
            model.add_exactly_one(options.values())
            stop_vars[key] = (arrival, departure, options)
            if key in seed_stops:
                hint = seed_stops[key]
                model.add_hint(arrival, hint.arrival_s)
                model.add_hint(departure, hint.departure_s)
                model.add_hint(end, hint.departure_s + station.clearance_s)
                model.add_hint(size, hint.departure_s + station.clearance_s - hint.arrival_s)
                for track_id, chosen in options.items():
                    model.add_hint(chosen, int(track_id == hint.track_id))
            if key in previous_stops and key not in departures:
                change = model.new_int_var(0, scenario.horizon_s, f"change_{key}")
                model.add_abs_equality(change, departure - previous_stops[key].departure_s)
                cost_terms.append(change)  # small stability penalty
                if key in seed_stops:
                    model.add_hint(
                        change, abs(seed_stops[key].departure_s - previous_stops[key].departure_s)
                    )
        final_arrival = stop_vars[(train.id, train.route[-1])][0]
        delay = model.new_int_var(0, scenario.horizon_s, f"delay_{train.id}")
        model.add_max_equality(delay, [0, final_arrival - train.due_s])
        if (train.id, train.route[-1]) in seed_stops:
            model.add_hint(
                delay, max(0, seed_stops[train.id, train.route[-1]].arrival_s - train.due_s)
            )
        weight = train.priority * (
            5 if strategy == "passenger" and train.kind == "passenger" else 1
        )
        cost_terms.extend([100 * weight * delay, final_arrival])
        for origin, destination in zip(train.route, train.route[1:]):
            section = section_for(scenario, origin, destination)
            hold = train.section_hold_s.get(section.id, 0)
            windows = duration_windows(train, section, origin, scenario.horizon_s)
            multiplier = 1.15 if strategy == "eco" else 1.0
            key = (train.id, origin)
            start = stop_vars[key][1]
            end = stop_vars[(train.id, destination)][0]
            if key in locked_moves:
                old_start, old_end, _ = locked_moves[key]
                model.add(start == old_start)
                model.add(end == old_end)
                duration = old_end - old_start
            else:
                durations = [math.ceil(d * multiplier) + hold for _, _, d in windows]
                if len(windows) == 1:
                    duration = durations[0]
                else:
                    duration = model.new_int_var(min(durations), max(durations), f"duration_{key}")
                    regimes = []
                    for n, ((lo, hi, _), value) in enumerate(zip(windows, durations)):
                        regime = model.new_bool_var(f"speed_regime_{key}_{n}")
                        model.add(start >= lo).only_enforce_if(regime)
                        model.add(start < hi).only_enforce_if(regime)
                        model.add(duration == value).only_enforce_if(regime)
                        regimes.append(regime)
                    model.add_exactly_one(regimes)
                model.add(end == start + duration)
            resource_end = model.new_int_var(0, scenario.horizon_s, f"release_{key}")
            model.add(resource_end == end + clearance_s(train, section))
            # Pairwise constraints preserve parallel operation outside a whole-section recovery.
            if any(section.id in t.section_recovery_all_tracks for t in scenario.trains):
                traversal = model.new_interval_var(
                    start,
                    duration + clearance_s(train, section),
                    resource_end,
                    f"recovery_peer_{key}",
                )
                section_intervals[section.id].append((train.id, traversal))
            if section.id in train.section_recovery_all_tracks:
                recovery = model.new_fixed_size_interval_var(start, hold, f"recovery_{key}")
                recoveries.append((section.id, train.id, recovery))
            if (train.id, destination) in seed_stops:
                model.add_hint(
                    resource_end,
                    seed_stops[train.id, destination].arrival_s + clearance_s(train, section),
                )
            main_options = {}
            for track in allowed_main_tracks(section, origin):
                resource = f"main_track:{section.id}:{track.id}"
                chosen = model.new_bool_var(f"main_track_{key}_{track.id}")
                main_options[track.id] = chosen
                interval = model.new_optional_interval_var(
                    start,
                    duration + clearance_s(train, section),
                    resource_end,
                    chosen,
                    f"movement_{key}_{track.id}",
                )
                resources[resource].append(interval)
                if key in locked_moves:
                    model.add(chosen == int(track.id == locked_moves[key][2]))
                if key in seed_moves:
                    model.add_hint(chosen, int(track.id == seed_moves[key].main_track_id))
                for block in scenario.blocks:
                    if block.kind == "signal" and block.resource in (
                        resource,
                        f"section:{section.id}",
                    ):
                        before = model.new_bool_var(f"before_{key}_{track.id}_{block.id}")
                        model.add(start < block.start_s).only_enforce_if([chosen, before])
                        model.add(start >= block.end_s).only_enforce_if([chosen, before.Not()])
                        if key in seed_stops:
                            model.add_hint(before, int(seed_stops[key].departure_s < block.start_s))
            model.add_exactly_one(main_options.values())
            if section.shared_resources:
                interval = model.new_interval_var(
                    start,
                    duration + clearance_s(train, section),
                    resource_end,
                    f"junction_movement_{key}",
                )
                for shared in section.shared_resources:
                    resources[shared].append(interval)
            move_vars.append((train.id, section.id, origin, destination, start, end, main_options))
    for resource, intervals in merged_closures(scenario).items():
        for i, (start, end) in enumerate(intervals):
            resources[resource].append(
                model.new_fixed_size_interval_var(start, end - start, f"closure_{resource}_{i}")
            )
    for section_id, train_id, recovery in recoveries:
        for peer_id, traversal in section_intervals[section_id]:
            if peer_id != train_id:
                model.add_no_overlap([recovery, traversal])
    for intervals in resources.values():
        model.add_no_overlap(intervals)
    model.minimize(sum(cost_terms))
    model_error = model.validate()
    if model_error:
        return result("INVALID", diagnostics=[model_error])
    remaining = time_budget_s - (time.perf_counter() - started) - 0.02
    if remaining <= 0:
        return result("UNKNOWN", diagnostics=["Time budget exhausted while building model"])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_search_workers = 4
    solver.parameters.random_seed = 7
    status = solver.solve(model)
    status_name = solver.status_name(status)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return result(
            "INVALID" if status == cp_model.MODEL_INVALID else status_name,
            diagnostics=["No feasible plan returned; never apply an unverified plan"],
        )
    plan = Plan(
        id=f"{strategy}-{uuid.uuid4().hex[:10]}",
        scenario_id=scenario.id,
        state_version=scenario.state_version,
        strategy=strategy,
        solver_status=status_name,
        objective_value=solver.objective_value,
        stops=[
            Stop(
                train_id=key[0],
                station_id=key[1],
                arrival_s=solver.value(a),
                departure_s=solver.value(d),
                track_id=next(t for t, chosen in options.items() if solver.value(chosen)),
            )
            for key, (a, d, options) in stop_vars.items()
        ],
        movements=[
            Movement(
                train_id=t,
                section_id=s,
                origin=o,
                destination=d,
                start_s=solver.value(start),
                end_s=solver.value(end),
                hold_s=next(train for train in scenario.trains if train.id == t).section_hold_s.get(
                    s, 0
                ),
                main_track_id=next(t for t, chosen in options.items() if solver.value(chosen)),
            )
            for t, s, o, d, start, end, options in move_vars
        ],
    )
    violations = validate_plan(scenario, plan, previous)
    if violations:
        return result("INVALID", diagnostics=[f"{v.code}: {v.message}" for v in violations])
    for train in scenario.trains:
        for stop in (s for s in plan.stops if s.train_id == train.id):
            wait = stop.departure_s - stop.arrival_s - train.min_dwell_s
            if wait > 0:
                plan.explanations.append(
                    f"{train.id}: ожидание на {stop.station_id} {wait} с сверх минимальной "
                    "стоянки; план учитывает занятость ресурсов и ограничения отправления."
                )
        first = next(
            s for s in plan.stops if s.train_id == train.id and s.station_id == train.route[0]
        )
        if first.arrival_s > train.release_s:
            plan.explanations.append(
                f"{train.id}: допуск на участок через {first.arrival_s - train.release_s} с "
                "после готовности; до допуска поезд находится вне моделируемого участка."
            )
    if strategy == "eco":
        plan.explanations.append(
            "Для новых движений запас ходового времени 15%. Выигрыш энергии проверяется "
            "отдельно по профилям; минимум энергии не доказан."
        )
    return result(status_name, plan)
