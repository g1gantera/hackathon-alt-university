import pytest
from pydantic import ValidationError

from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.resource_roster import model_roster
from backend.app.schemas import Scenario
from backend.app.validation.plan import validate_plan
from backend.tests.test_regional_operations import small


def rotation():
    scenario = small()
    scenario.trains[1].route = ["b", "a"]
    scenario.metadata["resource_roster"] = {
        "source": "test model",
        "units": [
            dict(
                id=kind,
                kind=kind,
                initial_station="a",
                available_s=200,
                unavailable_s=12000,
                turnaround_s=600,
                train_ids=["0", "1"],
            )
            for kind in ("locomotive", "wagons", "crew")
        ],
    }
    return Scenario.model_validate(scenario.model_dump())


def test_solver_delays_return_until_same_stock_and_crew_ready():
    scenario = rotation()
    result = solve_plan(scenario, time_budget_s=5)
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(scenario, result.plan)
    stops = {(s.train_id, s.station_id): s for s in result.plan.stops}
    assert stops["0", "a"].departure_s >= 200
    assert stops["1", "b"].departure_s >= stops["0", "b"].departure_s + 600
    bad = result.plan.model_copy(deep=True)
    next(s for s in bad.stops if s.train_id == "1" and s.station_id == "b").arrival_s = stops[
        "0", "b"
    ].departure_s
    assert any(v.code == "RESOURCE_AVAILABILITY" for v in validate_plan(scenario, bad))


def test_short_crew_window_is_infeasible_and_old_plan_cannot_bypass():
    scenario = rotation()
    previous = solve_plan(scenario, time_budget_s=5).plan
    scenario.metadata["resource_roster"]["units"][-1]["unavailable_s"] = 201
    scenario = Scenario.model_validate(scenario.model_dump())
    result = solve_plan(scenario, time_budget_s=5, previous=previous)
    assert result.status == "INFEASIBLE"
    assert result.plan is None
    assert build_baseline(scenario, time_budget_s=1).plan is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "teleport", "capacity"])
def test_roster_rejects_invalid_assignments(change):
    scenario = rotation().model_dump()
    units = scenario["metadata"]["resource_roster"]["units"]
    if change == "missing":
        units.pop()
    if change == "duplicate":
        units.append(units[0].copy())
    if change == "teleport":
        units[0]["initial_station"] = "c"
    if change == "capacity":
        units[0]["max_mass_kg"] = 1
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario)


def test_model_inventory_is_explicit_and_round_trips():
    scenario = small()
    plan = build_baseline(scenario).plan
    roster = model_roster(scenario, plan)
    assert len(roster.units) == 6
    scenario.metadata["resource_roster"] = roster.model_dump()
    scenario = Scenario.model_validate(scenario.model_dump())
    assert not validate_plan(scenario, plan)
