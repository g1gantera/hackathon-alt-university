"""Main-line capacity is distinct from station paths and whole-section blocks."""

import pytest

from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Block, MainTrack, Scenario
from backend.app.validation.plan import validate_plan


def double_track(small, *, directional=True):
    scenario = small.model_copy(deep=True)
    scenario.sections[0].main_tracks = [
        MainTrack(id="1", direction="a_to_b" if directional else "both"),
        MainTrack(id="2", direction="b_to_a" if directional else "both"),
    ]
    return scenario


def run(scenario, method, previous=None):
    result = (
        build_baseline(scenario)
        if method == "baseline"
        else solve_plan(scenario, time_budget_s=1, previous=previous)
    )
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(scenario, result.plan, previous)
    return result.plan


@pytest.mark.parametrize("method", ["baseline", "solver"])
def test_opposing_trains_can_overlap_on_distinct_directional_tracks(small, method):
    scenario = double_track(small)
    plan = run(scenario, method)
    first, second = plan.movements
    assert first.main_track_id == "1"
    assert second.main_track_id == "2"
    assert max(first.start_s, second.start_s) < min(first.end_s, second.end_s)


@pytest.mark.parametrize("method", ["baseline", "solver"])
@pytest.mark.parametrize("kind", ["closure", "signal"])
def test_one_track_block_leaves_other_available(small, method, kind):
    scenario = double_track(small, directional=False)
    scenario.blocks = [
        Block(id="one", resource="main_track:AB:1", start_s=0, end_s=4000, kind=kind)
    ]
    plan = run(scenario, method)
    assert all(m.main_track_id == "2" for m in plan.movements)


@pytest.mark.parametrize("method", ["baseline", "solver"])
@pytest.mark.parametrize("kind", ["closure", "signal"])
def test_whole_section_block_applies_to_every_track(small, method, kind):
    scenario = double_track(small)
    scenario.blocks = [Block(id="all", resource="section:AB", start_s=0, end_s=600, kind=kind)]
    plan = run(scenario, method)
    assert all(m.start_s >= 600 for m in plan.movements)


@pytest.mark.parametrize("method", ["baseline", "solver"])
def test_global_and_individual_closures_are_merged(small, method):
    scenario = double_track(small)
    scenario.blocks = [
        Block(id="global", resource="section:AB", start_s=0, end_s=500),
        Block(id="track", resource="main_track:AB:1", start_s=200, end_s=700),
    ]
    plan = run(scenario, method)
    assert next(m for m in plan.movements if m.main_track_id == "1").start_s >= 700
    assert next(m for m in plan.movements if m.main_track_id == "2").start_s >= 500


@pytest.mark.parametrize("method", ["baseline", "solver"])
def test_closed_direction_does_not_silently_authorize_contraflow(small, method):
    scenario = double_track(small)
    scenario.blocks = [Block(id="closed", resource="main_track:AB:1", start_s=0, end_s=500)]
    plan = run(scenario, method)
    forward = next(m for m in plan.movements if m.origin == "A")
    reverse = next(m for m in plan.movements if m.origin == "B")
    assert forward.main_track_id == "1" and forward.start_s >= 500
    assert reverse.main_track_id == "2" and reverse.start_s < 500


def test_validator_rejects_unknown_main_track_and_wrong_direction(small):
    scenario = double_track(small)
    plan = run(scenario, "solver")
    plan.movements[0].main_track_id = "2"
    assert "DIRECTION" in {v.code for v in validate_plan(scenario, plan)}
    plan.movements[0].main_track_id = "missing"
    assert "MAIN_TRACK" in {v.code for v in validate_plan(scenario, plan)}


def test_validator_detects_conflict_if_overlapping_trains_use_same_track(small):
    scenario = double_track(small, directional=False)
    plan = run(scenario, "solver")
    first, second = plan.movements
    assert first.main_track_id != second.main_track_id
    second.main_track_id = first.main_track_id
    errors = validate_plan(scenario, plan)
    assert any(
        v.code == "RESOURCE_CONFLICT" and v.resource.startswith("main_track:") for v in errors
    )


@pytest.mark.parametrize("kind", ["closure", "signal"])
def test_validator_independently_checks_individual_block(small, kind):
    scenario = double_track(small)
    plan = run(scenario, "solver")
    move = plan.movements[0]
    scenario.blocks = [
        Block(
            id="new",
            resource=f"main_track:AB:{move.main_track_id}",
            start_s=0,
            end_s=4000,
            kind=kind,
        )
    ]
    assert kind.upper() in {v.code for v in validate_plan(scenario, plan)}


def test_replan_locks_started_main_track_and_refuses_teleport(small):
    scenario = double_track(small, directional=False)
    previous = run(scenario, "solver")
    started = min(previous.movements, key=lambda m: m.start_s)
    scenario.now_s = started.start_s + 1
    scenario.state_version += 1
    plan = run(scenario, "solver", previous)
    assert next(m for m in plan.movements if m.train_id == started.train_id) == started
    scenario.blocks = [
        Block(
            id="occupied",
            resource=f"main_track:AB:{started.main_track_id}",
            start_s=scenario.now_s,
            end_s=scenario.now_s + 100,
        )
    ]
    result = solve_plan(scenario, time_budget_s=1, previous=previous)
    assert result.status == "INFEASIBLE"
    assert result.plan is None


def test_contract_rejects_duplicate_main_track_and_unknown_block(small):
    data = small.model_dump()
    data["sections"][0]["main_tracks"] = [{"id": "1"}, {"id": "1"}]
    with pytest.raises(ValueError, match="Duplicate main track"):
        Scenario.model_validate(data)
    data = small.model_dump()
    data["blocks"] = [{"id": "unknown", "resource": "main_track:AB:7", "start_s": 0, "end_s": 20}]
    with pytest.raises(ValueError, match="Unknown blocked resource"):
        Scenario.model_validate(data)


def test_single_track_legacy_contract_and_metadata(small):
    data = small.model_dump()
    data["sections"][0].pop("main_tracks")
    data["metadata"] = {"traffic": {"kind": "synthetic", "observed": False}}
    restored = Scenario.model_validate(data)
    assert restored.sections[0].main_tracks == [MainTrack(id="1")]
    assert restored.metadata["traffic"]["observed"] is False
