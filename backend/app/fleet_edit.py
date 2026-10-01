"""Atomic fleet edits: preserve every retained reservation and book new trains around them."""

import copy
import math
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .integration import LogicSimulator, config_for, project_plan, scenario_for
from .metrics.quality import calculate_metrics, profile_plan
from .planning.baseline import build_baseline
from .planning.common import clearance_s
from .scenarios import corridor_scenario
from .schemas import Block, Plan, Scenario
from .switches import reservations
from .validation.plan import validate_plan

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


class Addition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["passenger", "freight"]
    direction: Literal[1, -1]
    ready_in_s: int = Field(default=120, ge=1, le=14400)


class FleetEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: str
    add: list[Addition] = Field(default_factory=list, max_length=5)
    remove: list[str] = Field(default_factory=list, max_length=15)


def prepare_edit(state, edit):
    started = time.perf_counter()
    state = copy.deepcopy(state)
    if state["epoch"] != edit.epoch:
        raise ValueError("Запуск изменился. Обновите страницу.")
    if state["awaiting_plan"]:
        raise ValueError("Сначала примените план после сбоя.")
    now = state["sim_time_s"]
    old = Plan.model_validate(state["active_plan"]["_native"])
    scenario = scenario_for(state)
    if now >= scenario.horizon_s:
        raise ValueError("Завершён горизонт планирования. Начните новый запуск.")
    ids = {t.id for t in scenario.trains}
    removed = set(edit.remove)
    if len(removed) != len(edit.remove) or not removed <= ids:
        raise ValueError("Список удаления содержит неизвестные или повторные номера.")
    if not edit.add and not removed:
        raise ValueError("Нет изменений состава.")
    if not 5 <= len(ids) - len(removed) + len(edit.add) <= 20:
        raise ValueError("В сценарии должно оставаться от 5 до 20 поездов.")
    if any(s.train_id in removed and s.arrival_s <= now for s in old.stops):
        raise ValueError("Удалять можно только поезда, ещё не допущенные на участок.")
    retained = old.model_copy(deep=True)
    retained.stops = [s for s in old.stops if s.train_id not in removed]
    retained.movements = [m for m in old.movements if m.train_id not in removed]
    cancelled = list(scenario.metadata.get("cancelled_trains", []))
    for train in scenario.trains:
        if train.id in removed:
            cancelled.append(
                {
                    "id": train.id,
                    "cancelled_at_s": now,
                    "energy_kwh": train.auxiliary_power_w
                    * max(0, now - train.release_s)
                    / 3_600_000,
                }
            )
    scenario.metadata["cancelled_trains"] = cancelled
    scenario.metadata["cancelled_energy_kwh"] = sum(t["energy_kwh"] for t in cancelled)
    scenario.trains = [t for t in scenario.trains if t.id not in removed]
    retained_scenario = scenario.model_copy(deep=True)
    # Build closures for ALL reservations; unchanged trains keep their exact schedule.
    busy = []
    stations = {s.id: s for s in scenario.stations}
    sections = {s.id: s for s in scenario.sections}
    trains = {t.id: t for t in scenario.trains}
    for stop in retained.stops:
        busy.append(
            (
                f"track:{stop.station_id}:{stop.track_id}",
                stop.arrival_s,
                stop.departure_s + stations[stop.station_id].clearance_s,
            )
        )
    for move in retained.movements:
        section = sections[move.section_id]
        end = move.end_s + clearance_s(trains[move.train_id], section)
        for resource in [
            f"main_track:{section.id}:{move.main_track_id}",
            *section.shared_resources,
        ]:
            busy.append((resource, move.start_s, end))
        if section.id in trains[move.train_id].section_recovery_all_tracks and move.hold_s:
            busy.append((f"section:{section.id}", move.start_s, move.start_s + move.hold_s))
    busy.extend(
        (r["resource"], r["start_s"], r["end_s"])
        for r in reservations(retained_scenario.model_dump(), retained.model_dump())
    )
    templates = corridor_scenario().trains
    added = []
    for item in edit.add:
        template = next(t for t in templates if t.kind == item.kind).model_copy(deep=True)
        runtime = template.due_s - template.release_s
        if item.direction == -1:
            template.route.reverse()
        template.id = f"USR-{'P' if item.kind == 'passenger' else 'F'}-{uuid.uuid4().hex[:6]}"
        template.release_s = math.ceil(now) + item.ready_in_s
        template.due_s = template.release_s + runtime
        template.priority = max(1, round(state["settings"][item.kind + "_weight"]))
        added.append(template)
    if added:
        insertion = scenario.model_copy(deep=True)
        insertion.now_s = 0
        insertion.trains = added
        insertion.blocks.extend(
            Block(id=f"fleet-reserved-{i}", resource=r, start_s=a, end_s=b, kind="closure")
            for i, (r, a, b) in enumerate(busy)
        )
        insertion = Scenario.model_validate(insertion.model_dump())
        result = build_baseline(insertion, time_budget_s=3)
        if result.plan is None:
            raise ValueError(
                "Новые поезда не помещаются в расписание: " + "; ".join(result.diagnostics)
            )
        retained.stops.extend(result.plan.stops)
        retained.movements.extend(result.plan.movements)
    scenario.trains.extend(added)
    state["constraint_version"] += 1
    scenario.state_version = state["constraint_version"] + 1
    retained.state_version = scenario.state_version
    retained.id = "fleet-" + uuid.uuid4().hex[:10]
    retained.strategy = "balanced"
    retained.solver_status = "FEASIBLE"
    retained.objective_value = None
    retained.explanations = [
        "Состав изменён пакетом. Сохранены пути и времена существующих поездов; новые рейсы размещены в свободных интервалах."
    ]
    errors = validate_plan(scenario, retained, old)
    if errors:
        raise ValueError(
            "Изменение не прошло проверку: " + "; ".join(v.message for v in errors[:3])
        )
    state["scenario"] = scenario.model_dump()
    positions = {s["id"]: s["position_m"] for s in state["topology"]["stations"]}
    state["fleet"] = [
        dict(
            t.model_dump(),
            type=t.kind,
            number=t.id,
            direction=1 if positions[t.route[-1]] > positions[t.route[0]] else -1,
            destination=t.route[-1],
        )
        for t in scenario.trains
    ]
    profiles = profile_plan(scenario, retained)
    forecast = calculate_metrics(
        scenario, retained, config_for(state), previous=old, profiles=profiles
    )
    retained.elapsed_ms = (time.perf_counter() - started) * 1000
    state["active_plan"] = project_plan(state, scenario, retained, profiles, forecast)
    # Compare future alternatives against the same fleet, not the removed demand.
    state["baseline"] = copy.deepcopy(state["active_plan"])
    state["baseline_scenario"] = scenario.model_dump()
    state["state_version"] += 1
    return state


@router.post("/batch")
async def edit_fleet(body: FleetEdit, request: Request):
    import asyncio

    from . import main

    main.require(request)
    sim = main.sim
    if not isinstance(sim, LogicSimulator):
        raise HTTPException(409, "Изменение состава доступно для движка logic.")
    if sim.replanning:
        raise HTTPException(409, "Дождитесь текущего расчёта.")
    version, epoch = sim.state["constraint_version"], sim.state["epoch"]
    sim.replanning = True
    try:
        from .integration import compact_state

        candidate = await asyncio.to_thread(prepare_edit, compact_state(sim.state), body)
        if (
            main.sim is not sim
            or sim.state["constraint_version"] != version
            or sim.state["epoch"] != epoch
        ):
            raise HTTPException(409, "Условия изменились во время расчёта. Повторите операцию.")
        # Playback controls may have changed while calculation was in progress.
        candidate.update(running=sim.state["running"], speed=sim.state["speed"])
        sim.state = candidate
        sim.plans.clear()
        sim._validation_cache = None
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    finally:
        sim.replanning = False
    main.emit(
        "simulation.changed", {"action": "fleet", "added": len(body.add), "removed": body.remove}
    )
    main.publish()
    return sim.snapshot()
