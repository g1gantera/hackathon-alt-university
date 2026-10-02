import random

import pytest
from sample_network import demo_scenario

from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Block, Scenario
from backend.app.validation.plan import validate_plan


def solved(scenario, **kwargs):
    result = solve_plan(scenario, time_budget_s=1, **kwargs)
    assert result.plan is not None, result.diagnostics
    assert result.status in ("FEASIBLE", "OPTIMAL")
    assert not validate_plan(scenario, result.plan, kwargs.get("previous"))
    return result.plan


@pytest.mark.parametrize("strategy", ["balanced", "passenger", "eco"])
def test_opposing_trains_have_exclusive_reservations(small, strategy):
    plan = solved(small, strategy=strategy)
    first, second = sorted(plan.movements, key=lambda m: m.start_s)
    train = next(t for t in small.trains if t.id == first.train_id)
    assert second.start_s >= first.end_s + 10 + train.length_m / 5


def test_closure_and_overlapping_duplicate_closures_are_merged(small):
    small.blocks = [
        Block(id="a", resource="section:AB", start_s=0, end_s=400),
        Block(id="b", resource="section:AB", start_s=200, end_s=500),
    ]
    plan = solved(small)
    assert min(m.start_s for m in plan.movements) >= 500


def test_signal_blocks_entry(small):
    small.blocks = [Block(id="signal", resource="section:AB", start_s=0, end_s=400, kind="signal")]
    plan = solved(small)
    assert min(m.start_s for m in plan.movements) >= 400


def test_all_horizon_closed_is_infeasible(small):
    small.blocks = [Block(id="closed", resource="section:AB", start_s=0, end_s=small.horizon_s)]
    result = solve_plan(small, time_budget_s=1)
    assert result.status == "INFEASIBLE" and result.plan is None


def test_live_signal_preserves_started_movement(small):
    old = solved(small)
    started = min(old.movements, key=lambda m: m.start_s)
    small.now_s = (started.start_s + started.end_s) // 2
    small.state_version += 1
    small.blocks = [
        Block(
            id="signal",
            resource="section:AB",
            start_s=small.now_s,
            end_s=small.now_s + 500,
            kind="signal",
        )
    ]
    new = solved(small, previous=old)
    assert next(m for m in new.movements if m.train_id == started.train_id) == started


def test_closure_over_started_movement_cannot_teleport_train(small):
    old = solved(small)
    started = min(old.movements, key=lambda m: m.start_s)
    small.now_s = started.start_s + 1
    small.state_version += 1
    small.blocks = [
        Block(id="closed", resource="section:AB", start_s=small.now_s, end_s=small.now_s + 300)
    ]
    result = solve_plan(small, previous=old, time_budget_s=1)
    assert result.status == "INFEASIBLE"
    assert result.plan is None


def test_replanning_without_previous_state_is_rejected(small):
    small.now_s = 50
    assert solve_plan(small).status == "INVALID"


def test_baseline_is_feasible_and_deterministic(small):
    first, second = build_baseline(small), build_baseline(small)
    assert first.plan is not None
    assert not validate_plan(small, first.plan)
    assert first.plan.movements == second.plan.movements
    assert first.plan.stops == second.plan.stops


def test_delay_is_enforced(small):
    small.trains[0].not_before_s["A"] = 500
    plan = solved(small)
    assert next(m for m in plan.movements if m.train_id == "P1").start_s >= 500


def test_tiny_budget_is_unknown_not_infeasible(small):
    result = solve_plan(small, time_budget_s=1e-9)
    assert result.status == "UNKNOWN" and result.plan is None


@pytest.mark.parametrize("seed", range(5))
def test_randomized_closures_remain_feasible_and_valid(small, seed):
    rng = random.Random(seed)
    small.blocks = [
        Block(
            id=str(i),
            resource="section:AB",
            start_s=(start := rng.randint(0, 600)),
            end_s=start + rng.randint(20, 150),
        )
        for i in range(5)
    ]
    solved(small)


def test_contract_rejects_disconnected_routes(small):
    data = small.model_dump()
    data["trains"][0]["route"] = ["A", "missing"]
    with pytest.raises(ValueError):
        Scenario.model_validate(data)


def test_track_closure_forces_other_track(small):
    small.blocks = [Block(id="track", resource="track:A:1", start_s=0, end_s=4000)]
    plan = solved(small)
    assert all(s.track_id == "2" for s in plan.stops if s.station_id == "A")


def test_fcfs_waits_for_own_previous_junction_release():
    scenario = demo_scenario()
    scenario.trains = scenario.trains[:1]
    result = build_baseline(scenario)
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(scenario, result.plan)
    moves = {m.section_id: m for m in result.plan.movements}
    assert moves["CD"].start_s >= moves["BC"].end_s + 50


def test_full_network_baseline_and_solver_warm_start():
    scenario = demo_scenario()
    baseline = build_baseline(scenario)
    assert baseline.plan is not None, baseline.diagnostics
    result = solve_plan(scenario, time_budget_s=1.5)
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(scenario, result.plan)


def test_replan_rejects_incomplete_previous_movements(small):
    old = solved(small)
    small.now_s = 10
    old.movements.pop()
    assert solve_plan(small, previous=old).status == "INVALID"
