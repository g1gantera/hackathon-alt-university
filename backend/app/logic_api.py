"""Read-only access to native logic calculations through the shared backend."""

import asyncio
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .domain import ROOT
from .integration import LogicSimulator, config_for, scenario_for
from .metrics.economics import EconomicsRates, compare_plan_costs
from .metrics.load import calculate_track_load
from .metrics.quality import calculate_metrics
from .schemas import Plan, Scenario
from .validation.plan import validate_plan

router = APIRouter(prefix="/api/logic", tags=["logic"])


def simulator(request, role="viewer"):
    from . import main

    main.require(request, role)
    if not isinstance(main.sim, LogicSimulator):
        raise HTTPException(409, "Logic engine is not active")
    return main.sim


def context(state):
    return {
        "epoch": state["epoch"],
        "constraint_version": state["constraint_version"],
        "sim_time_s": state["sim_time_s"],
        "plan_id": state["active_plan"]["id"],
    }


def diagnose(state):
    scenario = scenario_for(state)
    native = Plan.model_validate(state["active_plan"]["_native"])
    forecast = calculate_metrics(scenario, native, config_for(state), previous=native)
    violations = validate_plan(scenario, native, native)
    # Still report physical causes when version validation short-circuits first.
    if native.state_version != scenario.state_version:
        current_version = native.model_copy(update={"state_version": scenario.state_version})
        violations += validate_plan(scenario, current_version, native)
    return {
        "context": context(state),
        "scenario": scenario.model_dump(),
        "plan": native.model_dump(),
        "forecast": forecast.model_dump(),
        "violations": [v.model_dump() for v in violations],
        "needs_replan": state["awaiting_plan"] or bool(violations),
    }


@router.get("/metrics")
async def native_metrics(request: Request):
    sim = simulator(request)
    state = copy.deepcopy(sim.state)
    actual = sim.snapshot()["metrics"]
    report = await asyncio.to_thread(diagnose, state)
    return {
        "context": report["context"],
        "actual": actual,
        "forecast": report["forecast"],
        "violations": report["violations"],
    }


@router.get("/track-load")
async def track_load(
    request: Request, start_s: int = Query(0, ge=0), end_s: int | None = Query(None, ge=1)
):
    state = copy.deepcopy(simulator(request).state)
    scenario = scenario_for(state)
    plan = Plan.model_validate(state["active_plan"]["_native"])
    try:
        report = await asyncio.to_thread(
            calculate_track_load,
            scenario,
            plan,
            window_start_s=start_s,
            window_end_s=end_s,
            previous=plan,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"context": context(state), "report": report.model_dump()}


class EconomicRequest(BaseModel):
    reference_plan_id: str
    rates: EconomicsRates


@router.get("/economics/rates-example")
async def example_rates(request: Request):
    simulator(request)
    return json.loads((ROOT / "config/economics.example.json").read_text())


@router.post("/economics")
async def economics(body: EconomicRequest, request: Request):
    sim = simulator(request)
    state, candidates = copy.deepcopy(sim.state), copy.deepcopy(sim.plans)
    available = {state["active_plan"]["id"]: state["active_plan"], **candidates}
    if body.reference_plan_id not in available:
        raise HTTPException(
            404, "Choose the active plan or a current calculated candidate as reference"
        )
    plans = [Plan.model_validate(p["_native"]) for p in available.values()]
    reference = Plan.model_validate(available[body.reference_plan_id]["_native"])
    previous = Plan.model_validate(state["active_plan"]["_native"])
    try:
        report = await asyncio.to_thread(
            compare_plan_costs,
            scenario_for(state),
            plans,
            reference,
            body.rates,
            config_for(state),
            previous=previous,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    # Recommendation only; applying still requires the existing dispatcher endpoint.
    return {"context": context(state), "report": report.model_dump()}


class RealismRequest(BaseModel):
    runs: int = Field(default=1, ge=1, le=10)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)


def realism_report(base, runs, seed):
    from .realism.service import run_realism

    with TemporaryDirectory(prefix="railflow-realism-") as folder:
        run_realism(Scenario.model_validate(base), folder, runs=runs, seed=seed)
        return json.loads((Path(folder) / "report.json").read_text())


@router.post("/realism")
async def realism(body: RealismRequest, request: Request):
    # This bounded offline experiment does not change live constraints or apply a plan.
    sim = simulator(request, "dispatcher")
    from . import main

    if sim.replanning or getattr(main.app.state, "realism_running", False):
        raise HTTPException(409, "A calculation is already running; retry when it completes")
    state = copy.deepcopy(sim.state)
    main.app.state.realism_running = True
    try:
        report = await asyncio.to_thread(
            realism_report, state["baseline_scenario"], body.runs, body.seed
        )
    finally:
        main.app.state.realism_running = False
    return {
        "context": context(state),
        "scope": "offline_baseline_corridor",
        "applied_to_live": False,
        "report": report,
    }
