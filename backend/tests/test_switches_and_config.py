import copy

import pytest

from backend.app.metrics.quality import MetricConfig
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Block, Switch
from backend.app.switches import reservations
from backend.app.validation.plan import validate_plan


def test_weighted_formula_thresholds_and_unknowns():
    config = MetricConfig()
    components = dict.fromkeys(config.weights, 0.8)
    assert config.score(components) == 80
    assert config.category(80) == "Норма"
    assert config.category(50) == "Внимание"
    assert config.category(49) == "Критично"
    components["arrival_accuracy"] = None
    assert config.score(components) == 80
    assert sum(
        v for v in config.contributions(components).values() if v is not None
    ) == pytest.approx(80)
    assert config.category(None) == "Нет данных"
    unknown = MetricConfig(weights={k: float(k == "arrival_accuracy") for k in config.weights})
    assert unknown.score(components) is None
    components["energy"] = 0
    assert config.score(components) > 0
    config.formula = "weighted_geometric"
    assert config.score(components) == 0
    with pytest.raises(ValueError):
        MetricConfig(formula='__import__("os")')
    with pytest.raises(ValueError):
        MetricConfig(weights=dict.fromkeys(config.weights, 0.3))


def test_switch_closure_and_conflicting_routes(small):
    scenario = copy.deepcopy(small)
    for station in scenario.stations:
        station.switch = Switch(clearance_s=20)
    scenario.blocks.append(
        Block(id="switch-work", resource="switch:A:throat", start_s=0, end_s=200, kind="closure")
    )
    for result in [build_baseline(scenario), solve_plan(scenario, time_budget_s=1)]:
        assert result.plan, result.diagnostics
        assert not validate_plan(scenario, result.plan)
        locks = reservations(scenario.model_dump(), result.plan.model_dump())
        assert locks
        assert all(r["start_s"] >= 200 for r in locks if r["station_id"] == "A")
    plan = build_baseline(scenario).plan
    lock = next(
        r for r in reservations(scenario.model_dump(), plan.model_dump()) if r["station_id"] == "A"
    )
    scenario.blocks.append(
        Block(
            id="new-fault",
            resource="switch:A:throat",
            start_s=lock["start_s"],
            end_s=lock["end_s"],
            kind="closure",
        )
    )
    assert any(
        v.code == "CLOSURE" and v.resource == "switch:A:throat"
        for v in validate_plan(scenario, plan)
    )


def test_switch_separates_trains_on_independent_parallel_tracks(small):
    from backend.app.schemas import MainTrack

    scenario = small.model_copy(deep=True)
    scenario.sections[0].main_tracks = [MainTrack(id="1"), MainTrack(id="2")]
    first = scenario.trains[0]
    scenario.trains = [first, first.model_copy(update={"id": "P2"}, deep=True)]
    simultaneous = build_baseline(scenario).plan
    assert simultaneous
    assert len({m.start_s for m in simultaneous.movements}) == 1
    for station in scenario.stations:
        station.switch = Switch(clearance_s=20)
    assert any(
        v.code == "RESOURCE_CONFLICT" and v.resource.startswith("switch:")
        for v in validate_plan(scenario, simultaneous)
    )
    for result in [build_baseline(scenario), solve_plan(scenario, time_budget_s=1)]:
        assert result.plan, result.diagnostics
        assert not validate_plan(scenario, result.plan)
        starts = sorted(m.start_s for m in result.plan.movements)
        assert starts[1] - starts[0] >= 20
