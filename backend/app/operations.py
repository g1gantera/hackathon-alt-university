"""Auditable operational estimates. All unsourced coefficients remain explicit."""

from collections import defaultdict


def stock_rotation(scenario, plan, turnaround_s=1800, crew_shift_s=8 * 3600):
    """Greedy reusable stock/crew chains at the SAME station; no empty teleporting.

    New units indicate required initial fleet, not availability of real KTZ assets.
    Crew relief is allocated at scheduled stops when the next leg exceeds a shift.
    """
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    fleet = []
    crews = []
    assignments = []
    ordered = sorted(scenario.trains, key=lambda t: stops[t.id, t.route[0]].departure_s)
    for t in ordered:
        start = stops[t.id, t.route[0]].departure_s
        end = stops[t.id, t.route[-1]].arrival_s
        ready = [
            u
            for u in fleet
            if u["kind"] == t.kind and u["station"] == t.route[0] and u["available_s"] <= start
        ]
        unit = min(ready, key=lambda u: u["available_s"]) if ready else None
        if unit is None:
            unit = {
                "id": f"UNIT-{len(fleet) + 1:03}",
                "kind": t.kind,
                "station": t.route[0],
                "available_s": 0,
                "initial_station": t.route[0],
                "turns": [],
            }
            fleet.append(unit)
        unit["turns"].append(
            {
                "train_id": t.id,
                "origin": t.route[0],
                "destination": t.route[-1],
                "start_s": start,
                "end_s": end,
            }
        )
        unit.update(station=t.route[-1], available_s=end + turnaround_s)
        crew = None
        reliefs = []
        for m in sorted((m for m in plan.movements if m.train_id == t.id), key=lambda m: m.start_s):
            if crew is None or m.end_s - crew["shift_start_s"] > crew_shift_s:
                candidates = [
                    c
                    for c in crews
                    if c["station"] == m.origin
                    and c["available_s"] <= m.start_s
                    and m.end_s - c["shift_start_s"] <= crew_shift_s
                ]
                crew = (
                    min(candidates, key=lambda c: c["available_s"])
                    if candidates
                    else {
                        "id": f"CREW-{len(crews) + 1:03}",
                        "station": m.origin,
                        "shift_start_s": m.start_s,
                        "available_s": m.start_s,
                    }
                )
                if not candidates:
                    crews.append(crew)
                reliefs.append({"station_id": m.origin, "crew_id": crew["id"], "time_s": m.start_s})
            if m.end_s - crew["shift_start_s"] > crew_shift_s:
                return {
                    "feasible": False,
                    "reason": "A single leg exceeds the assumed crew shift; another relief point is required",
                    "train_id": t.id,
                }
            crew.update(station=m.destination, available_s=m.end_s + 300)
        assignments.append({"train_id": t.id, "unit_id": unit["id"], "crew_reliefs": reliefs})
    return {
        "feasible": True,
        "fleet_required": len(fleet),
        "crews_required": len(crews),
        "units": fleet,
        "assignments": assignments,
        "turnaround_s": turnaround_s,
        "crew_shift_s": crew_shift_s,
        "source": "simulation_assumption",
        "note": "Оценка минимально необходимого парка жадным алгоритмом, не доказанный минимум. Состав и локомотив считаются одной совместимой единицей. Начальный парк не подтверждён. Смена бригад только на станциях; 8 часов — параметр модели, не норматив.",
    }


def wear_forecast(scenario, plan, now_s, design_million_tonnes=1000):
    trains = {t.id: t for t in scenario.trains}
    sections = {s.id: s for s in scenario.sections}
    load = defaultdict(float)
    for m in plan.movements:
        if m.end_s > now_s:
            continue
        t = trains[m.train_id]
        s = sections[m.section_id]
        speed = s.length_m / max(1, m.end_s - m.start_s - m.hold_s) * 3.6
        load[f"main_track:{s.id}:{m.main_track_id}"] += (
            t.mass_kg / 1e9 * (max(1, speed / 60) ** 2) * (1 + abs(s.grade_permille) / 100)
        )
    initial = scenario.metadata.get("track_wear", {})
    return {
        "source": "uncalibrated_simulation",
        "formula": "base_pct + 100 × Σ(mass_kg/1e9 × max(1,v_kmh/60)^2 × (1+abs(grade_permille)/100)) / design_million_tonnes",
        "design_million_tonnes": design_million_tonnes,
        "note": "Расчётный индекс главных путей, не диагностика рельсов. При неизвестном исходном износе условно принято 0%. Учтены завершённые проходы; коэффициенты требуют калибровки. Прогноз сам не вводит ограничений скорости.",
        "tracks": [
            {
                "resource": r,
                "equivalent_million_tonnes": v,
                "base_pct": initial.get(r, {}).get("wear_pct", 0),
                "estimated_pct": min(
                    100, initial.get(r, {}).get("wear_pct", 0) + 100 * v / design_million_tonnes
                ),
            }
            for r, v in sorted(load.items())
        ],
    }


def empty_wagon_allocation(scenario, supplies, demands):
    """Integer minimum-cost flow on the model graph, minimizing empty wagon metres."""
    from ortools.graph.python import min_cost_flow

    ids = {s.id: i for i, s in enumerate(scenario.stations)}
    if (set(supplies) | set(demands)) - set(ids):
        raise ValueError("Unknown station")
    if any(not isinstance(n, int) or n < 0 for n in [*supplies.values(), *demands.values()]):
        raise ValueError("Counts must be nonnegative integers")
    if sum(supplies.values()) != sum(demands.values()):
        raise ValueError("Supply and demand must balance")
    total = sum(supplies.values())
    flow = min_cost_flow.SimpleMinCostFlow()
    arcs = []
    for s in scenario.sections:
        for a, b, direction in (
            (s.station_a, s.station_b, "a_to_b"),
            (s.station_b, s.station_a, "b_to_a"),
        ):
            if not any(t.direction in ("both", direction) for t in s.main_tracks):
                continue
            flow.add_arc_with_capacity_and_unit_cost(ids[a], ids[b], total, round(s.length_m))
            arcs.append((a, b, s.id))
    for sid, i in ids.items():
        flow.set_node_supply(i, supplies.get(sid, 0) - demands.get(sid, 0))
    status = flow.solve()
    if status != flow.OPTIMAL:
        return {"feasible": False, "reason": "Demand cannot be reached on connected model lines"}
    return {
        "feasible": True,
        "empty_wagon_km": flow.optimal_cost() / 1000,
        "flows": [
            {"origin": a, "destination": b, "section_id": s, "wagons": flow.flow(i)}
            for i, (a, b, s) in enumerate(arcs)
            if flow.flow(i)
        ],
        "note": "Транспортная задача по расстоянию. Назначение вагонов не создаёт автоматически рейсы и не подтверждает их вместимость в графике.",
    }


def recovery_report(scenario, plan):
    from .validation.plan import validate_plan

    violations = validate_plan(
        scenario, plan.model_copy(update={"state_version": scenario.state_version}), plan
    )
    affected = []
    for b in scenario.blocks:
        if b.end_s <= scenario.now_s:
            continue
        moving = [
            m.train_id
            for m in plan.movements
            if m.start_s <= scenario.now_s < m.end_s
            and b.resource
            in (f"section:{m.section_id}", f"main_track:{m.section_id}:{m.main_track_id}")
        ]
        affected.append(
            {
                "block_id": b.id,
                "resource": b.resource,
                "reopens_s": b.end_s,
                "trains_on_resource": moving,
                "action": "Остановить автоматическое применение. Нужны место остановки и подтверждённый план эвакуации/восстановления."
                if moving and b.kind == "closure"
                else "Дождаться окончания ограничения либо подтверждения восстановления, затем пересчитать план.",
            }
        )
    return {
        "violations": [v.model_dump() for v in violations],
        "restrictions": affected,
        "automatic_resume_allowed": not violations,
        "note": "История закрытия и начатое движение не переписываются. Удержание не означает, что реальный поезд мгновенно остановлен.",
    }
