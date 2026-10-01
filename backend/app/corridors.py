"""Selectable, independent corridor runs; source geometry retained offline."""

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
CORRIDORS = {
    "astana1_ereymentau": ("Астана-1 — Ерейментау", ROOT / "data/akmola/astana1_ereymentau.json"),
    "atbasar_esil": ("Атбасар — Есиль", ROOT / "data/akmola/atbasar_esil.json"),
    "astana_burabay": (
        "Астана Нурлы Жол — Курорт Бурабай",
        ROOT / "data/akmola/astana_burabay.json",
    ),
    "kokshetau_burabay": ("Кокшетау — Курорт Бурабай", ROOT / "data/akmola/kokshetau_burabay.json"),
    "kokshetau": ("Кокшетау — Астана Нурлы Жол", ROOT / "data/corridor/infrastructure.json"),
    "astana_atbasar": ("Астана Нурлы Жол — Атбасар", ROOT / "data/akmola/astana_atbasar.json"),
    "astana_ereymentau": (
        "Астана Нурлы Жол — Ерейментау",
        ROOT / "data/akmola/astana_ereymentau.json",
    ),
}
router = APIRouter(prefix="/api/corridors", tags=["corridors"])


class Selection(BaseModel):
    epoch: str
    corridor: str
    traffic_profile: Literal["demo", "reference_day"] = "demo"
    service_date: date = date(2026, 10, 2)


@router.get("")
async def corridors(request: Request):
    from . import main

    main.require(request, "viewer")
    return [{"id": k, "name": v[0]} for k, v in CORRIDORS.items()]


@router.post("/select")
async def select(body: Selection, request: Request):
    import asyncio

    from . import main
    from .integration import LogicSimulator

    main.require(request)
    old = main.sim
    if body.corridor not in CORRIDORS:
        raise HTTPException(422, "Неизвестное направление")
    if old.state["epoch"] != body.epoch or old.replanning:
        raise HTTPException(409, "Дождитесь расчёта и обновите страницу")
    old.replanning = True
    try:
        candidate = await asyncio.to_thread(
            LogicSimulator, body.corridor, body.traffic_profile, body.service_date.isoformat()
        )
        if main.sim is not old or old.state["epoch"] != body.epoch:
            raise HTTPException(409, "Запуск изменился")
        candidate.state["control_mode"] = old.state.get("control_mode", "manual")
        main.sim = candidate
        main.emit("corridor.changed", {"corridor": body.corridor})
        main.publish()
        if candidate.state["control_mode"] == "automatic":
            main.queue_replan()
        return {"epoch": candidate.state["epoch"], "corridor": body.corridor}
    finally:
        old.replanning = False
