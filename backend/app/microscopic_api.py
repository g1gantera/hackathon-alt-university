"""Isolated execution trials of the transferred era engine, under existing auth."""

import asyncio
import copy
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .microscopic.bridge import from_integrated, geometry, network
from .microscopic.engine import Engine
from .microscopic.models import IncidentSpec, Settings

router = APIRouter(prefix="/api/execution", tags=["detailed-execution"])
_lock = asyncio.Lock()
_run = None


class Create(BaseModel):
    epoch: str
    source: Literal["integrated", "passing", "overtaking", "priority", "closure"] = "integrated"


class Step(BaseModel):
    run_id: str
    revision: int = Field(ge=0)
    seconds: float = Field(gt=0, le=30, allow_inf_nan=False)


class Disturbance(BaseModel):
    run_id: str
    revision: int = Field(ge=0)
    incident: IncidentSpec


class Clear(BaseModel):
    run_id: str
    revision: int = Field(ge=0)
    incident_id: str


def authorized(request, write=False):
    from . import main

    main.require(request, "dispatcher" if write else "viewer")
    if main.sim.state.get("execution") is not None:
        raise HTTPException(410, "Детальное движение теперь в основном движке; используйте /api/state и /api/simulation/*")
    return main


def checked(run_id=None, revision=None):
    from . import main

    if _run is None:
        raise HTTPException(404, "Создайте детальный запуск")
    if _run["source_epoch"] != main.sim.state["epoch"]:
        raise HTTPException(409, "Основной запуск изменился. Создайте детальный запуск заново.")
    if _run.get("failed"):
        raise HTTPException(409, "Проверка движения завершилась нарушением. Создайте новый запуск.")
    if run_id is not None and (run_id != _run["id"] or revision != _run["revision"]):
        raise HTTPException(409, "Детальное состояние изменилось; обновите его перед командой")
    return _run


def snapshot(run, with_geometry=False):
    value = run["engine"].snapshot()
    value.update(
        run_id=run["id"],
        revision=run["revision"],
        source=run["source"],
        source_plan_id=run["source_plan_id"],
        source_epoch=run["source_epoch"],
        isolation_note="Отдельная проверка исполнения. Её позиции и метрики не заменяют состояние основного графика.",
        limitations=[
            "SIM-пути не сопоставлены с рёбрами OSM.",
            "Энергия этого режима: упрощённая модель era без уклона и рекуперации.",
            "Назначения парка integrated в этом режиме не исполняются.",
            "Времена графика являются целями; разрешение на движение выдаёт блочный диспетчер.",
        ],
    )
    if with_geometry:
        value["geometry"] = geometry(run["engine"])
    return value


@router.post("/create")
async def create(body: Create, request: Request):
    global _run
    main = authorized(request, True)
    async with _lock:
        if main.sim.replanning or body.epoch != main.sim.state["epoch"]:
            raise HTTPException(409, "Состояние изменилось или выполняется расчёт")
        source = copy.deepcopy(main.sim.state)

        def prepare():
            if body.source == "integrated":
                return from_integrated(source)
            engine = Engine(network(), config=Settings())
            engine.load_demo(body.source)
            return engine

        try:
            engine = await asyncio.to_thread(prepare)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if (
            main.sim.state["epoch"] != body.epoch
            or main.sim.state["active_plan"]["id"] != source["active_plan"]["id"]
            or main.sim.state.get("constraint_version") != source.get("constraint_version")
            or (body.source == "integrated" and main.sim.state["sim_time_s"] != source["sim_time_s"])
        ):
            engine.history.db.close()
            raise HTTPException(409, "График изменился во время подготовки; повторите импорт")
        if _run is not None:
            _run["engine"].history.db.close()
        _run = dict(
            id=uuid.uuid4().hex,
            revision=0,
            engine=engine,
            source=body.source,
            source_epoch=body.epoch,
            source_plan_id=source["active_plan"]["id"],
        )
        main.sim.state["running"] = False
        main.publish()
        return await asyncio.to_thread(snapshot, _run, True)


@router.get("/state")
async def state(request: Request):
    authorized(request)
    async with _lock:
        return await asyncio.to_thread(snapshot, checked(), True)


@router.post("/step")
async def step(body: Step, request: Request):
    authorized(request, True)
    async with _lock:
        run = checked(body.run_id, body.revision)

        def advance():
            frames = []
            remaining = body.seconds
            while remaining > 1e-9:
                dt = min(0.2, remaining)
                run["engine"].advance(dt)
                errors = run["engine"].invariant_errors()
                if errors:
                    raise ValueError("; ".join(errors))
                frames.append(
                    {
                        "time_s": run["engine"].sim_time,
                        "trains": [
                            {
                                "id": t.spec.id,
                                "distance_m": t.x,
                                "speed_mps": t.speed,
                                "position": run["engine"].network.position(
                                    *run["engine"].network.locate(t.route, t.x)[1:]
                                ),
                            }
                            for t in run["engine"].trains.values()
                        ],
                    }
                )
                remaining -= dt
            return frames

        run["revision"] += 1  # Even a failed command consumed its revision.
        try:
            frames = await asyncio.to_thread(advance)
        except ValueError as error:
            run["failed"] = True
            raise HTTPException(409, "Движение остановлено: " + str(error)) from error
        result = await asyncio.to_thread(snapshot, run, True)
        result["frames"] = frames
        return result


@router.post("/incidents")
async def incident(body: Disturbance, request: Request):
    authorized(request, True)
    async with _lock:
        run = checked(body.run_id, body.revision)
        try:
            await asyncio.to_thread(run["engine"].add_incidents, [body.incident])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        run["revision"] += 1
        return await asyncio.to_thread(snapshot, run, True)


@router.post("/clear")
async def clear(body: Clear, request: Request):
    authorized(request, True)
    async with _lock:
        run = checked(body.run_id, body.revision)
        if body.incident_id not in run["engine"].incidents:
            raise HTTPException(404, "Неизвестное ограничение")
        await asyncio.to_thread(run["engine"].clear_incident, body.incident_id)
        run["revision"] += 1
        return await asyncio.to_thread(snapshot, run, True)
