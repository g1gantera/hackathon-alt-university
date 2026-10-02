"""Solve only unexecuted journeys; recorded movement is immutable evidence."""

import copy
import math
import time

from .planning.service import plan_alternatives
from .schemas import Plan, Scenario
from .validation.plan import validate_plan as native_validate


def planning_scenario(state):
    from .integration import scenario_for

    raw = scenario_for(state).model_dump()
    actual = state["execution"]["trains"]
    if any(r["moving"] for r in actual.values()):
        raise ValueError("Finish entered legs before changing future routes")
    now = math.ceil(state["sim_time_s"])
    raw.update(now_s=0, state_version=state["constraint_version"] + 1)
    remaining = []
    raw["metadata"]["execution_origin_arrivals"] = {}
    raw["metadata"]["reserve_receiving_tracks"] = True
    for t in raw["trains"]:
        r = actual[t["id"]]
        if r["complete"]:
            continue
        t["route"] = t["route"][r["leg"] :]
        t["release_s"] = max(t["release_s"], now + 1, math.ceil(r["ready"]))
        t["due_s"] = max(t["due_s"], t["release_s"])
        t["not_before_s"] = {k: v for k, v in t["not_before_s"].items() if k in t["route"]}
        t["manual_station_tracks"] = {
            k: v for k, v in t["manual_station_tracks"].items() if k in t["route"]
        }
        if r["admitted"]:
            t["release_s"] = now
            t["not_before_s"][t["route"][0]] = max(
                now + 1, math.ceil(r["ready"]), t["not_before_s"].get(t["route"][0], 0)
            )
            raw["metadata"]["execution_origin_arrivals"][t["id"]] = now
            t["manual_station_tracks"][t["route"][0]] = r["track"]
        sections = {
            s["id"]
            for s in raw["sections"]
            if s["station_a"] in t["route"] and s["station_b"] in t["route"]
        }
        for field in ("manual_main_tracks", "section_hold_s"):
            t[field] = {k: v for k, v in t[field].items() if k in sections}
        t["section_recovery_all_tracks"] = [
            s for s in t["section_recovery_all_tracks"] if s in sections
        ]
        remaining.append(t)
    raw["trains"] = remaining
    tids = {t["id"]: t for t in remaining}
    for tid, r in actual.items():
        if r["move"] and state["sim_time_s"] < r["tail_until"]:
            m = r["move"]
            raw["blocks"].append(
                dict(
                    id="tail:" + tid,
                    resource=f"main_track:{m['section_id']}:{m['main_track_id']}",
                    start_s=0,
                    end_s=math.ceil(r["tail_until"]),
                    kind="closure",
                )
            )
        if r["complete"] and state["sim_time_s"] < r["release"]:
            station = next(t["route"][-1] for t in state["scenario"]["trains"] if t["id"] == tid)
            raw["blocks"].append(
                dict(
                    id="terminal:" + tid,
                    resource=f"track:{station}:{r['track']}",
                    start_s=0,
                    end_s=math.ceil(r["release"]),
                    kind="closure",
                )
            )
    roster = raw["metadata"].get("resource_roster")
    if roster:
        units = []
        for unit in roster["units"]:
            original = unit["train_ids"]
            unit["train_ids"] = [tid for tid in original if tid in tids]
            if not unit["train_ids"]:
                continue
            first = unit["train_ids"][0]
            at = original.index(first)
            unit["initial_station"] = tids[first]["route"][0]
            if at:
                unit["available_s"] = max(
                    unit["available_s"],
                    math.ceil(actual[original[at - 1]]["release"] + unit["turnaround_s"]),
                )
            units.append(unit)
        roster["units"] = units
    return None if not remaining else Scenario.model_validate(raw)


def build_execution_plans(state):
    from .integration import config_for, project_plan

    scenario = planning_scenario(state)
    if scenario is None:
        return dict(plans=[], diagnostics=["Все рейсы завершены"], elapsed_s=0, within_budget=True)
    budget = state.get("planning_budget_s", 15 if scenario.metadata.get("network") else 10)
    started = time.perf_counter()
    # Establish one safe continuation before spending time comparing strategies.
    result = plan_alternatives(
        scenario, config_for(state), strategies=("balanced",), time_budget_s=max(1, budget - 0.8)
    )
    remaining = budget - 0.8 - (time.perf_counter() - started)
    if result.candidates and remaining > 1 and not scenario.metadata.get("network"):
        alternatives = plan_alternatives(
            scenario, config_for(state), strategies=("passenger", "eco"), time_budget_s=remaining
        )
        result.candidates.extend(alternatives.candidates)
        result.diagnostics.extend(alternatives.diagnostics)
    plans = []
    for candidate in result.candidates:
        plan = project_plan(state, scenario, candidate.plan, candidate.profiles, candidate.metrics)
        remaining_native = copy.deepcopy(plan["_native"])
        full_stops, full_moves = [], []
        for tid, r in state["execution"]["trains"].items():
            for old in r["actual_stops"]:
                # The present stop stays open; its observed arrival is shown in the ledger.
                if old["departure_s"] is not None:
                    full_stops.append(
                        dict(
                            old,
                            arrival_s=math.floor(old["arrival_s"]),
                            departure_s=math.ceil(old["departure_s"]),
                        )
                    )
            full_moves.extend(
                dict(m, start_s=math.floor(m["start_s"]), end_s=math.ceil(m["end_s"]))
                for m in r["actual_moves"]
            )
        for move in plan["movements"]:
            move["leg"] += state["execution"]["trains"][move["train_id"]]["leg"]
        plan["movements"] = full_moves + plan["movements"]
        plan["stops"] = full_stops + plan["stops"]
        plan["_remaining_native"] = remaining_native
        plan["_remaining_scenario"] = scenario.model_dump()
        plan["_execution_revision"] = state["execution"]["revision"]
        plan["_native"]["stops"] = plan["stops"]
        fields = (
            "train_id",
            "section_id",
            "main_track_id",
            "origin",
            "destination",
            "start_s",
            "end_s",
            "hold_s",
        )
        plan["_native"]["movements"] = [{k: m[k] for k in fields} for m in plan["movements"]]
        plan["_profiles"] = dict(state["active_plan"].get("_profiles", {})) | plan["_profiles"]
        plan["forecast"]["scope"] = "remaining_journeys"
        plans.append(plan)
    return dict(
        plans=plans,
        diagnostics=result.diagnostics,
        message="; ".join(result.diagnostics),
        elapsed_s=round(time.perf_counter() - started, 3),
        within_budget=time.perf_counter() - started <= 5,
    )


def validate_execution_plan(state, plan):
    if any(r["moving"] for r in state["execution"]["trains"].values()):
        return [
            {"code": "EXECUTION_IN_PROGRESS", "message": "Entered routes are still being executed"}
        ]
    if plan.get("_execution_revision") != state["execution"]["revision"]:
        return [{"code": "EXECUTION_CHANGED", "message": "Actual movement changed; recalculate"}]
    scenario = planning_scenario(state)
    if scenario is None:
        return []
    return [
        v.model_dump()
        for v in native_validate(scenario, Plan.model_validate(plan["_remaining_native"]))
    ]
