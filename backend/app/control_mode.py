"""Dispatcher-issued route clearances; automatic mode uses validated plans."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/control", tags=["control"])


def clearance_key(plan_id, move):
    return f"{plan_id}:{move['train_id']}:{move['section_id']}:{move['start_s']}"


def authorized(state, move):
    if move["start_s"] < state["sim_time_s"]:
        return True  # An entered route remains committed when the mode/plan changes.
    return state.get("control_mode", "manual") == "automatic" or clearance_key(
        state["active_plan"]["id"], move
    ) in state.get("route_clearances", [])


def limit_time(state, target):
    pending = [
        m
        for m in state["active_plan"]["movements"]
        if m["start_s"] > state["sim_time_s"] and not authorized(state, m)
    ]
    boundary = min(
        (max(state["sim_time_s"], m["start_s"] - 0.001) for m in pending), default=target
    )
    return min(target, boundary)


class Mode(BaseModel):
    epoch: str
    mode: Literal["manual", "automatic"]


class Clearance(BaseModel):
    epoch: str
    plan_id: str
    train_id: str
    section_id: str


@router.post("/mode")
async def mode(body: Mode, request: Request):
    from . import main

    main.require(request)
    state = main.sim.state
    if state.get("engine") != "logic":
        raise HTTPException(409, "Режимы управления доступны для движка logic")
    if main.sim.replanning:
        raise HTTPException(409, "Дождитесь завершения расчёта")
    if state["epoch"] != body.epoch:
        raise HTTPException(409, "Запуск изменился")
    state["control_mode"] = body.mode
    state["route_clearances"] = []
    state["state_version"] += 1
    main.emit("control.mode_changed", {"mode": body.mode})
    main.publish()
    if body.mode == "automatic":
        for train in state["scenario"]["trains"]:
            train["manual_station_tracks"] = {}
            train["manual_main_tracks"] = {}
        state["constraint_version"] += 1
        state["awaiting_plan"] = True
        main.publish()
        main.queue_replan()
    return main.sim.snapshot()


@router.post("/authorize")
async def authorize(body: Clearance, request: Request):
    from . import main

    state = main.sim.state
    if state.get("engine") != "logic":
        raise HTTPException(409, "Режимы управления доступны для движка logic")
    main.require(request)
    if state.get("control_mode", "manual") != "manual":
        raise HTTPException(409, "Разрешения выдаёт автоматический режим")
    if state["epoch"] != body.epoch or state["active_plan"]["id"] != body.plan_id:
        raise HTTPException(409, "Маршрут изменился; проверьте новый план")
    if (
        main.sim.replanning
        or state["awaiting_plan"]
        or (main.sim.plan_violations() if state.get('execution') else main.validate_plan(state, state['active_plan']))
    ):
        raise HTTPException(409, "Сначала примените проверенный план")
    def pending(move):
        execution = state.get('execution')
        if execution:
            train = execution['trains'][move['train_id']]
            return not train['moving'] and not train['complete'] and train['leg'] == move['leg']
        return move['start_s'] > state['sim_time_s']

    move = next((m for m in state['active_plan']['movements']
                 if m['train_id'] == body.train_id and m['section_id'] == body.section_id
                 and pending(m)), None)
    if move is None:
        raise HTTPException(409, "Будущее отправление не найдено")
    key = clearance_key(body.plan_id, move)
    if key not in state.setdefault("route_clearances", []):
        state["route_clearances"].append(key)
        state["state_version"] += 1
        main.emit(
            "route.authorized",
            {
                "train_id": body.train_id,
                "section_id": body.section_id,
                "main_track_id": move["main_track_id"],
                "departure_s": move["start_s"],
            },
        )
    main.publish()
    return main.sim.snapshot()
