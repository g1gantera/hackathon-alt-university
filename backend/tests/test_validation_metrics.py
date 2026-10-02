import pytest

from backend.app.metrics.quality import MetricConfig, calculate_metrics
from backend.app.planning.service import plan_alternatives
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Block
from backend.app.validation.plan import validate_plan


@pytest.fixture
def plan(small):
    return solve_plan(small, time_budget_s=1).plan


def test_tampered_plan_detects_conflict_and_bad_continuity(small, plan):
    first, second = plan.movements
    second.start_s, second.end_s = first.start_s, first.end_s
    codes = {v.code for v in validate_plan(small, plan)}
    assert "RESOURCE_CONFLICT" in codes and "CONTINUITY" in codes


def test_missing_and_duplicate_stops_are_rejected(small, plan):
    plan.stops.pop()
    assert "STOP_SET" in {v.code for v in validate_plan(small, plan)}


def test_stale_plan_cannot_be_scored_as_applicable(small, plan):
    small.state_version += 1
    metrics = calculate_metrics(small, plan, MetricConfig())
    assert not metrics.applicable
    assert metrics.category == "Критично" and metrics.energy_kwh is None
    assert metrics.quality_index is None


def test_metrics_are_finite_forecasts_and_weights_change_without_rebuild(small, plan):
    default = calculate_metrics(small, plan, MetricConfig())
    custom = MetricConfig(
        weights={
            "punctuality": 1,
            "throughput": 0,
            "energy": 0,
            "conflicts": 0,
            "arrival_accuracy": 0,
        }
    )
    changed = calculate_metrics(small, plan, custom)
    assert default.kind == "forecast" and default.applicable
    assert default.conflict_count == 0
    assert 0 <= default.quality_index <= 100
    assert changed.quality_index == round(changed.components["punctuality"] * 100, 2)
    assert default.energy_kwh > 0 and default.reference_energy_kwh > 0


def test_invalid_weights_are_rejected():
    with pytest.raises(ValueError):
        MetricConfig(weights={"punctuality": 1})


def test_independent_checker_catches_impossible_speed(small, plan):
    move = plan.movements[0]
    move.end_s = move.start_s + 1
    assert "PHYSICS" in {v.code for v in validate_plan(small, plan)}


def test_checker_detects_modified_committed_track(small, plan):
    original = plan.model_copy(deep=True)
    move = min(original.movements, key=lambda m: m.start_s)
    small.now_s = move.start_s + 1
    stop = next(
        s for s in plan.stops if s.train_id == move.train_id and s.station_id == move.origin
    )
    stop.track_id = "2" if stop.track_id == "1" else "1"
    assert "COMMITTED" in {v.code for v in validate_plan(small, plan, original)}


def test_integration_returns_serializable_checked_candidates(small):
    result = plan_alternatives(small, MetricConfig(), time_budget_s=2)
    assert len(result.candidates) == 2
    assert not result.budget_exceeded
    for candidate in result.candidates:
        assert not validate_plan(small, candidate.plan)
        assert candidate.metrics.applicable
        for movement in candidate.plan.movements:
            profile = candidate.profiles[f"{movement.train_id}:{movement.section_id}"]
            assert profile.duration_s == pytest.approx(movement.end_s - movement.start_s, abs=1e-5)
    assert '"state_version":1' in result.model_dump_json()


def test_validator_detects_station_track_collision(small, plan):
    a = next(s for s in plan.stops if s.station_id == "A" and s.train_id == "P1")
    b = next(s for s in plan.stops if s.station_id == "A" and s.train_id == "F1")
    b.track_id = a.track_id
    b.arrival_s = a.arrival_s
    b.departure_s = a.departure_s
    assert any(
        v.code == "RESOURCE_CONFLICT" and v.resource.startswith("track:")
        for v in validate_plan(small, plan)
    )


def test_validator_catches_closure_on_an_otherwise_valid_plan(small, plan):
    movement = plan.movements[0]
    small.blocks = [
        Block(id="closed", resource="section:AB", start_s=movement.start_s, end_s=movement.end_s)
    ]
    assert "CLOSURE" in {v.code for v in validate_plan(small, plan)}


def test_validator_catches_insufficient_station_dwell(small, plan):
    plan.stops[0].departure_s = plan.stops[0].arrival_s
    assert "DWELL" in {v.code for v in validate_plan(small, plan)}
