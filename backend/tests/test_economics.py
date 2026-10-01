import math

import pytest

from app.metrics.economics import EconomicsRates, compare_plan_costs
from app.metrics.quality import MetricConfig, calculate_metrics
from app.planning.baseline import build_baseline
from app.schemas import MainTrack


def rates(**overrides):
    values = {
        "electricity_tenge_per_kwh": 50,
        "passenger_delay_tenge_per_hour": 0,
        "freight_delay_tenge_per_hour": 0,
        "assumptions_note": "Illustrative test values; no actual tariff is asserted.",
    }
    values.update(overrides)
    return EconomicsRates(**values)


def delayed_arrival(plan, train_id, extra_s, plan_id):
    changed = plan.model_copy(deep=True, update={"id": plan_id, "strategy": "eco"})
    move = next(m for m in changed.movements if m.train_id == train_id)
    move.end_s += extra_s
    stop = next(
        s for s in changed.stops if s.train_id == train_id and s.station_id == move.destination
    )
    stop.arrival_s += extra_s
    stop.departure_s += extra_s
    return changed


@pytest.fixture
def economic_pair(small):
    scenario = small.model_copy(deep=True)
    scenario.trains = scenario.trains[:1]
    scenario.trains[0].auxiliary_power_w = 0
    scenario.metadata["traffic"] = {"source": "synthetic"}
    scenario.metadata["assumptions"] = ["Level track; hypothetical traffic and energy tariff"]
    reference = build_baseline(scenario).plan
    assert reference is not None
    scenario.trains[0].due_s = reference.movements[0].end_s
    slower = delayed_arrival(reference, scenario.trains[0].id, 60, "slower")
    return scenario, reference, slower


@pytest.mark.parametrize(
    "field",
    [
        "electricity_tenge_per_kwh",
        "passenger_delay_tenge_per_hour",
        "freight_delay_tenge_per_hour",
    ],
)
@pytest.mark.parametrize("value", [-1, math.inf, -math.inf, math.nan])
def test_rates_require_finite_nonnegative_numbers(field, value):
    with pytest.raises(ValueError):
        rates(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"assumptions_note": ""},
        {"assumptions_note": "  "},
        {"max_extra_passenger_delay_s": -1},
        {"max_extra_passenger_delay_s": 1.5},
        {"max_extra_total_delay_s": -1},
        {"input_status": "operator_verified"},
    ],
)
def test_assumptions_and_guards_are_validated(overrides):
    with pytest.raises(ValueError):
        rates(**overrides)


def test_no_implicit_rates_are_supplied():
    with pytest.raises(ValueError):
        EconomicsRates(assumptions_note="Missing prices")


def test_lower_energy_cannot_override_passenger_guard(economic_pair):
    scenario, reference, slower = economic_pair
    report = compare_plan_costs(scenario, [slower], reference, rates(), MetricConfig())
    original, candidate = report.rows
    assert candidate.energy_kwh < original.energy_kwh
    assert candidate.generalized_cost_tenge < original.generalized_cost_tenge
    assert candidate.eligible is False
    assert candidate.max_extra_passenger_delay_s == 60
    assert candidate.worsened_passenger_trains == [scenario.trains[0].id]
    assert any("PASSENGER_DELAY" in reason for reason in candidate.reasons)
    assert report.selected_plan_id == reference.id


def test_break_even_delay_penalty_changes_selection(economic_pair):
    scenario, reference, slower = economic_pair
    config = MetricConfig()
    fast_metrics, slow_metrics = (
        calculate_metrics(scenario, p, config) for p in (reference, slower)
    )
    saving = 50 * (fast_metrics.energy_kwh - slow_metrics.energy_kwh)
    threshold = saving / ((slow_metrics.passenger_delay_s - fast_metrics.passenger_delay_s) / 3600)
    assert threshold > 0
    cheap_delay = compare_plan_costs(
        scenario,
        [slower],
        reference,
        rates(passenger_delay_tenge_per_hour=threshold * 0.9, max_extra_passenger_delay_s=60),
        config,
    )
    expensive_delay = compare_plan_costs(
        scenario,
        [slower],
        reference,
        rates(passenger_delay_tenge_per_hour=threshold * 1.1, max_extra_passenger_delay_s=60),
        config,
    )
    assert cheap_delay.selected_plan_id == slower.id
    assert expensive_delay.selected_plan_id == reference.id
    assert cheap_delay.rows[1].generalized_saving_tenge_vs_reference == pytest.approx(saving * 0.1)
    assert expensive_delay.rows[1].generalized_saving_tenge_vs_reference == pytest.approx(
        -saving * 0.1
    )


def test_passenger_guard_applies_individually_despite_improved_total(small):
    scenario = small.model_copy(deep=True)
    scenario.sections[0].main_tracks = [
        MainTrack(id="1", direction="a_to_b"),
        MainTrack(id="2", direction="b_to_a"),
    ]
    for train in scenario.trains:
        train.kind = "passenger"
    fast = build_baseline(scenario).plan
    assert fast is not None
    for train in scenario.trains:
        train.due_s = next(m.end_s for m in fast.movements if m.train_id == train.id)
    reference = delayed_arrival(fast, scenario.trains[1].id, 120, "reference")
    candidate = delayed_arrival(fast, scenario.trains[0].id, 60, "candidate")
    report = compare_plan_costs(scenario, [candidate], reference, rates(), MetricConfig())
    assert report.rows[1].total_delay_s < report.rows[0].total_delay_s
    assert report.rows[1].eligible is False
    assert report.rows[1].worsened_passenger_trains == [scenario.trains[0].id]


def test_optional_total_guard_limits_freight_delay(economic_pair):
    scenario, reference, slower = economic_pair
    scenario.trains[0].kind = "freight"
    report = compare_plan_costs(
        scenario,
        [slower],
        reference,
        rates(max_extra_total_delay_s=30),
        MetricConfig(),
    )
    candidate = report.rows[1]
    assert candidate.max_extra_passenger_delay_s == 0
    assert candidate.eligible is False
    assert any("TOTAL_DELAY" in reason for reason in candidate.reasons)


def test_invalid_candidate_has_no_cost(economic_pair):
    scenario, reference, slower = economic_pair
    slower.movements[0].main_track_id = "unknown"
    report = compare_plan_costs(scenario, [slower], reference, rates(), MetricConfig())
    assert report.rows[1].eligible is False
    assert report.rows[1].generalized_cost_tenge is None
    assert report.rows[1].energy_kwh is None
    assert any("MAIN_TRACK" in reason for reason in report.rows[1].reasons)
    assert report.selected_plan_id == reference.id


def test_invalid_reference_is_rejected(economic_pair):
    scenario, reference, slower = economic_pair
    reference.stops.pop()
    with pytest.raises(ValueError, match="Reference plan is not applicable"):
        compare_plan_costs(scenario, [slower], reference, rates(), MetricConfig())


def test_reference_is_included_and_zero_prices_retain_it_on_tie(economic_pair):
    scenario, reference, slower = economic_pair
    inputs = rates(electricity_tenge_per_kwh=0, max_extra_passenger_delay_s=60)
    report = compare_plan_costs(scenario, [reference, slower], reference, inputs, MetricConfig())
    assert len(report.rows) == 2
    assert all(row.eligible and row.generalized_cost_tenge == 0 for row in report.rows)
    assert report.selected_plan_id == reference.id
    assert report.rates == inputs
    assert report.kind == "model_cost_estimate"
    assert report.traffic_source == "synthetic"
    assert report.scenario_id == scenario.id and report.state_version == scenario.state_version
    assert report.operational_exactness is False
    assert report.assumptions == scenario.metadata["assumptions"]
    assert report.assumptions is not scenario.metadata["assumptions"]
    assert report.model_dump(mode="json")["rates"]["input_status"] == "illustrative"


def test_duplicate_ids_are_rejected(economic_pair):
    scenario, reference, slower = economic_pair
    with pytest.raises(ValueError, match="Duplicate plan ID"):
        compare_plan_costs(scenario, [slower, slower], reference, rates(), MetricConfig())
    slower.id = reference.id
    with pytest.raises(ValueError, match="Duplicate plan ID"):
        compare_plan_costs(scenario, [slower], reference, rates(), MetricConfig())


def test_previous_plan_is_forwarded_during_live_replanning(economic_pair):
    scenario, previous, _ = economic_pair
    scenario.now_s = previous.movements[0].start_s + 1
    scenario.state_version += 1
    reference = previous.model_copy(update={"state_version": scenario.state_version})
    report = compare_plan_costs(scenario, [], reference, rates(), MetricConfig(), previous=previous)
    assert report.selected_plan_id == reference.id
    with pytest.raises(ValueError, match="MISSING_PREVIOUS"):
        compare_plan_costs(scenario, [], reference, rates(), MetricConfig())


def test_cost_uses_separate_passenger_and_freight_positive_delay(small):
    scenario = small.model_copy(deep=True)
    plan = build_baseline(scenario).plan
    assert plan is not None
    for train in scenario.trains:
        arrival = next(m.end_s for m in plan.movements if m.train_id == train.id)
        train.due_s = arrival - (40 if train.kind == "passenger" else 80)
    report = compare_plan_costs(
        scenario,
        [],
        plan,
        rates(
            electricity_tenge_per_kwh=0,
            passenger_delay_tenge_per_hour=3600,
            freight_delay_tenge_per_hour=7200,
        ),
        MetricConfig(),
    )
    row = report.rows[0]
    assert row.electricity_cost_tenge == 0
    assert row.passenger_delay_penalty_tenge == 40
    assert row.freight_delay_penalty_tenge == 160
    assert row.generalized_cost_tenge == 200
