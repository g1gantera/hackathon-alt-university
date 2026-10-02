"""Read-only access to native logic calculations through the shared backend."""

import asyncio
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .domain import ROOT
from .integration import LogicSimulator, config_for, scenario_for, compact_state
from .metrics.economics import EconomicsRates, compare_plan_costs
from .metrics.load import calculate_track_load
from .metrics.quality import MetricConfig, calculate_metrics
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
    if 'execution' in state:
        forecast = copy.deepcopy(state['active_plan']['forecast'])
        errors = list(state['execution']['conflicts'])
        if state['awaiting_plan']:
            errors.append({'code':'ACTUAL_REPLAN','message':'Нужен прогноз от фактических позиций'})
            forecast.update(applicable=False,quality_index=None,energy_kwh=None,track_load=None)
        return {'context':context(state),'scenario':state['scenario'],
                'plan':state['active_plan']['_native'],'forecast':forecast,
                'violations':errors,'needs_replan':bool(errors)}
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
    state = compact_state(sim.state)
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
    state = compact_state(simulator(request).state)
    active = state['active_plan']
    scenario = Scenario.model_validate(active['_remaining_scenario']) if '_remaining_scenario' in active else scenario_for(state)
    plan = Plan.model_validate(active.get('_remaining_native', active['_native']))
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
    state = compact_state(sim.state)
    candidates = {key: {k: v for k, v in plan.items() if k != '_profiles'}
                  for key, plan in sim.plans.items()}
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
    state = compact_state(sim.state)
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


@router.get("/metric-config")
async def metric_config(request: Request):
    return config_for(simulator(request).state).model_dump()


@router.put("/metric-config")
async def update_metric_config(body: MetricConfig, request: Request):
    from . import main, metric_settings
    sim = simulator(request, "admin")
    if sim.replanning:
        raise HTTPException(409, "Wait until calculation completes")
    version = (sim.state["epoch"], sim.state["state_version"])
    sim.replanning = True
    try:
        # Re-score an isolated copy; viewers continue receiving the current state.
        def prepare():
            candidate = copy.copy(sim)
            candidate.state = compact_state(sim.state)
            candidate.plans = {}
            candidate.state["metric_config"] = body.model_dump()
            candidate.update_settings(candidate.state["settings"])
            return candidate
        candidate = await asyncio.to_thread(prepare)
        if version != (sim.state["epoch"], sim.state["state_version"]):
            raise HTTPException(409, "State changed; retry saving the settings")
        metric_settings.save(body)
        sim.state, sim.plans = candidate.state, {}
        sim._validation_cache = None
        main.emit("settings.updated", {"metric_config": body.model_dump()})
    finally:
        sim.replanning = False
        main.publish()
    return body.model_dump()


@router.get("/performance")
async def performance(request: Request):
    simulator(request)
    from . import main
    times = list(main.state_times)
    gaps = [b-a for a,b in zip(times,times[1:])]
    samples = list(main.paint_samples)
    latencies = sorted(s["event_to_ack_ms"] for s in samples)
    return {"ingestion": main.ingestion.status if main.ingestion else None,"state_events": len(times), "target_hz": 2,
            "mean_hz": (len(gaps)/sum(gaps)) if gaps and sum(gaps) else None,
            "max_stream_gap_ms": max(gaps, default=0)*1000,
            "paint_samples": samples,
            "paint_max_ms": max(latencies, default=None),
            "paint_p95_ms": latencies[min(len(latencies)-1,int(len(latencies)*.95))] if latencies else None,
            "measurement": "Start of snapshot construction, through normalization service, to browser acknowledgement after React commit and two animation frames; includes return network time. Visible tabs only; no guarantee on hidden tabs or other machines."}


@router.get("/operations")
async def operations_report(request: Request):
    from .operations import stock_rotation, wear_forecast, recovery_report
    state=compact_state(simulator(request).state)
    scenario=scenario_for(state);plan=Plan.model_validate(state["active_plan"]["_native"])
    def calculate():
        return {"context":context(state),"resource_roster":scenario.metadata.get("resource_roster"),"rotation":stock_rotation(scenario,plan),"wear":wear_forecast(scenario,plan,state["sim_time_s"]),"recovery":recovery_report(scenario,plan)}
    return await asyncio.to_thread(calculate)


class WagonDemand(BaseModel):
    supplies: dict[str,int]
    demands: dict[str,int]


@router.post("/empty-wagons")
async def empty_wagons(body: WagonDemand, request: Request):
    from .operations import empty_wagon_allocation
    scenario=scenario_for(simulator(request).state)
    try:return await asyncio.to_thread(empty_wagon_allocation,scenario,body.supplies,body.demands)
    except ValueError as error:raise HTTPException(422,str(error)) from error


@router.get("/regional-catalog")
async def regional_catalog(request: Request):
    simulator(request)
    from .regional import infrastructure
    data=infrastructure()
    return {"stations":data["stations"],"sections":data["sections"],"coverage":data["coverage"],"source":data["source"]}


@router.get("/resource-roster-template")
async def roster_template(request: Request):
    from .resource_roster import model_roster
    state = compact_state(simulator(request).state)
    return model_roster(scenario_for(state), Plan.model_validate(state["active_plan"]["_native"])).model_dump()
