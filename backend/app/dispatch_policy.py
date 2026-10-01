"""Dispatcher assignments and explicitly synthetic track-condition constraints."""

import copy
import math
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .integration import LogicSimulator, scenario_for
from .planning.common import committed_fields
from .schemas import Block, EntrySpeedLimit, Plan, Scenario

router = APIRouter(prefix="/api/dispatch", tags=["dispatch policy"])


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: str
    train_id: str
    station_tracks: dict[str, str] = Field(default_factory=dict)
    main_tracks: dict[str, str] = Field(default_factory=dict)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    epoch: str
    resource: str
    wear_pct: float | None = Field(default=None, ge=0, le=100)


def get_sim(request, role="dispatcher"):
    from . import main

    main.require(request, role)
    if not isinstance(main.sim, LogicSimulator):
        raise HTTPException(409, "Доступно для движка logic.")
    return main.sim


def checked_scenario(sim, epoch):
    if epoch != sim.state["epoch"] or sim.replanning:
        raise ValueError("Запуск изменился или выполняется расчёт. Повторите после обновления.")
    return scenario_for(sim.state)


def assign(sim, body):
    scenario = checked_scenario(sim, body.epoch)
    train = next((t for t in scenario.trains if t.id == body.train_id), None)
    if train is None:
        raise ValueError("Поезд не найден.")
    old = Plan.model_validate(sim.state["active_plan"]["_native"])
    _, _, locked_tracks, locked_moves = committed_fields(scenario, old)
    for sid, tid in body.station_tracks.items():
        if (train.id, sid) in locked_tracks and locked_tracks[train.id, sid] != tid:
            raise ValueError(
                "Прибытие или подход уже зафиксированы: станционный путь менять нельзя."
            )
    for move in old.movements:
        if (
            move.train_id == train.id
            and (train.id, move.origin) in locked_moves
            and move.section_id in body.main_tracks
            and body.main_tracks[move.section_id] != move.main_track_id
        ):
            raise ValueError("Поезд уже вошёл на перегон: путь менять нельзя.")
    train.manual_station_tracks = body.station_tracks
    train.manual_main_tracks = body.main_tracks
    return Scenario.model_validate(scenario.model_dump())


def resources(scenario):
    return {f"track:{s.id}:{t.id}" for s in scenario.stations for t in s.tracks} | {
        f"main_track:{s.id}:{t.id}" for s in scenario.sections for t in s.main_tracks
    }


def condition(sim, body):
    scenario = checked_scenario(sim, body.epoch)
    if body.resource not in resources(scenario):
        raise ValueError("Путь не найден.")
    now = math.floor(sim.state["sim_time_s"]) + 1
    if now >= scenario.horizon_s - 1:
        raise ValueError("Завершён горизонт планирования.")
    wear = copy.deepcopy(scenario.metadata.get("track_wear", {}))
    if body.wear_pct is None:
        wear.pop(body.resource, None)
    else:
        wear[body.resource] = {
            "wear_pct": body.wear_pct,
            "source": "dispatcher_simulation",
            "updated_at_s": sim.state["sim_time_s"],
        }
    scenario.metadata["track_wear"] = wear
    prefix = "wear:" + body.resource + ":"
    # Keep historical restrictions; only replace their future part.
    blocks = []
    for b in scenario.blocks:
        if b.id.startswith(prefix):
            if b.start_s >= now:
                continue
            b.end_s = min(b.end_s, now)
        blocks.append(b)
    scenario.blocks = blocks
    if body.wear_pct is not None and body.wear_pct >= 85:
        release = now
        plan = sim.state["active_plan"]
        if body.resource.startswith("main_track:"):
            _, sid, tid = body.resource.split(":")
            release = max(
                [now]
                + [
                    m["release_s"]
                    for m in plan["movements"]
                    if m["section_id"] == sid
                    and m["main_track_id"] == tid
                    and m["start_s"] <= sim.state["sim_time_s"] < m["release_s"]
                ]
            )
        else:
            _, sid, tid = body.resource.split(":")
            station = next(s for s in scenario.stations if s.id == sid)
            locked = committed_fields(scenario, Plan.model_validate(plan["_native"]))[2]
            release = max(
                [now]
                + [
                    s["departure_s"] + station.clearance_s
                    for s in plan["_native"]["stops"]
                    if s["station_id"] == sid
                    and s["track_id"] == tid
                    and (s["train_id"], sid) in locked
                ]
            )
        if release < scenario.horizon_s - 1:
            scenario.blocks.append(
                Block(
                    id=prefix + uuid.uuid4().hex[:8],
                    resource=body.resource,
                    start_s=release,
                    end_s=scenario.horizon_s - 1,
                    kind="closure",
                )
            )
    if body.resource.startswith("main_track:"):
        _, sid, _ = body.resource.split(":")
        section = next(s for s in scenario.sections if s.id == sid)
        speed_prefix = f"wear-speed:{sid}:"
        limits = []
        for limit in section.entry_speed_limits:
            if limit.id.startswith(speed_prefix):
                if limit.start_s >= now:
                    continue
                limit.end_s = min(limit.end_s, now)
            limits.append(limit)
        section.entry_speed_limits = limits
        values = [
            v["wear_pct"]
            for key, v in wear.items()
            if key.startswith(f"main_track:{sid}:") and v["wear_pct"] < 85
        ]
        worst = max(values, default=0)
        factor = 0.6 if worst >= 65 else 0.8 if worst >= 40 else 1
        if factor < 1:
            section.entry_speed_limits.append(
                EntrySpeedLimit(
                    id=speed_prefix + uuid.uuid4().hex[:8],
                    start_s=now,
                    end_s=scenario.horizon_s - 1,
                    speed_factor=factor,
                    reason="Учебная оценка износа; консервативное ограничение всего перегона",
                )
            )
    return Scenario.model_validate(scenario.model_dump())


def commit(sim, scenario, kind):
    from . import main

    sim.state["scenario"] = scenario.model_dump()
    sim.state["constraint_version"] += 1
    sim.state["state_version"] += 1
    sim.state["awaiting_plan"] = True
    sim.plans.clear()
    sim._validation_cache = None
    main.emit("dispatch.policy_changed", {"kind": kind})
    main.publish()
    main.queue_replan()


@router.get("/policy")
async def policy(request: Request):
    sim = get_sim(request, "viewer")
    scenario = scenario_for(sim.state)
    return {
        "epoch": sim.state["epoch"],
        "wear": scenario.metadata.get("track_wear", {}),
        "assignments": {
            t.id: {"station_tracks": t.manual_station_tracks, "main_tracks": t.manual_main_tracks}
            for t in scenario.trains
        },
        "assumption": "Учебный индекс износа, не обследование КТЖ. До 40% — без ограничения; 40–64% — скорость ×0.8; 65–84% — ×0.6; от 85% — закрытие пути после освобождения. Скорость ограничивается для всего перегона. Для станционных путей применяется только закрытие от 85%.",
    }


@router.post("/assignment")
async def save_assignment(body: Assignment, request: Request):
    sim = get_sim(request)
    try:
        scenario = assign(sim, body)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    commit(sim, scenario, "assignment")
    return {
        "accepted": True,
        "message": "Назначения сохранены. Примените один из проверенных вариантов.",
    }


@router.post("/condition")
async def save_condition(body: Condition, request: Request):
    sim = get_sim(request)
    try:
        scenario = condition(sim, body)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    commit(sim, scenario, "condition")
    return {
        "accepted": True,
        "message": "Оценка износа сохранена. Движение ожидает проверенного плана.",
    }


class CategoryChange(BaseModel):
    epoch: str
    train_id: str
    category: Literal["emergency", "passenger", "express_freight", "freight", "service"]


@router.post("/category")
async def save_category(body: CategoryChange, request: Request):
    sim = get_sim(request)
    try:
        scenario = checked_scenario(sim, body.epoch)
        train = next((t for t in scenario.trains if t.id == body.train_id), None)
        if train is None:
            raise ValueError("Поезд не найден")
        for item in scenario.trains:
            item.dispatch_category = item.dispatch_category or item.kind
        train.dispatch_category = body.category
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    commit(sim, scenario, "category")
    return {"accepted": True}
