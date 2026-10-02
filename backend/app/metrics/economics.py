"""Choose among checked plans using explicit energy prices and delay penalties."""

from typing import Literal

from pydantic import Field, field_validator

from backend.app.metrics.quality import MetricConfig, calculate_metrics
from backend.app.schemas import Model, Plan, Scenario
from backend.app.validation.plan import validate_plan


class EconomicsRates(Model):
    electricity_tenge_per_kwh: float = Field(ge=0)
    passenger_delay_tenge_per_hour: float = Field(ge=0)
    freight_delay_tenge_per_hour: float = Field(ge=0)
    max_extra_passenger_delay_s: int = Field(default=0, ge=0)
    max_extra_total_delay_s: int | None = Field(default=None, ge=0)
    assumptions_note: str = Field(min_length=1)
    input_status: Literal["illustrative", "user_supplied"] = "illustrative"

    @field_validator("assumptions_note")
    @classmethod
    def nonblank_note(cls, value):
        if not value.strip():
            raise ValueError("Explain the source and assumptions of the rates")
        return value.strip()


class EconomicRow(Model):
    plan_id: str
    strategy: str
    energy_kwh: float | None = None
    total_delay_s: int | None = None
    passenger_delay_s: int | None = None
    electricity_cost_tenge: float | None = None
    passenger_delay_penalty_tenge: float | None = None
    freight_delay_penalty_tenge: float | None = None
    generalized_cost_tenge: float | None = None
    electricity_saving_tenge_vs_reference: float | None = None
    generalized_saving_tenge_vs_reference: float | None = None
    max_extra_passenger_delay_s: int | None = None
    worsened_passenger_trains: list[str] = Field(default_factory=list)
    eligible: bool
    reasons: list[str] = Field(default_factory=list)


class EconomicComparison(Model):
    kind: Literal["model_cost_estimate"] = "model_cost_estimate"
    scenario_id: str
    state_version: int
    traffic_source: str
    operational_exactness: bool = False
    assumptions: list[str] = Field(default_factory=list)
    rates: EconomicsRates
    reference_plan_id: str
    selected_plan_id: str
    rows: list[EconomicRow]
    caveats: list[str]


def _passenger_lateness(scenario: Scenario, plan: Plan):
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    return {
        train.id: max(0, stops[train.id, train.route[-1]].arrival_s - train.due_s)
        for train in scenario.trains
        if train.kind == "passenger"
    }


def _score(scenario, plan, rates, metric_config, previous):
    violations = validate_plan(scenario, plan, previous)
    if violations:
        return EconomicRow(
            plan_id=plan.id,
            strategy=plan.strategy,
            eligible=False,
            reasons=[f"{v.code}: {v.message}" for v in violations],
        )
    try:
        metrics = calculate_metrics(scenario, plan, metric_config, previous=previous)
    except ValueError as error:
        return EconomicRow(
            plan_id=plan.id,
            strategy=plan.strategy,
            eligible=False,
            reasons=[f"METRICS_INVALID: {error}"],
        )
    if not metrics.applicable or metrics.energy_kwh is None:
        return EconomicRow(
            plan_id=plan.id,
            strategy=plan.strategy,
            eligible=False,
            reasons=["PROFILE_INVALID: plan lacks applicable physical profiles and metrics"],
        )
    electricity = metrics.energy_kwh * rates.electricity_tenge_per_kwh
    passenger = metrics.passenger_delay_s / 3600 * rates.passenger_delay_tenge_per_hour
    freight = (
        (metrics.total_delay_s - metrics.passenger_delay_s)
        / 3600
        * rates.freight_delay_tenge_per_hour
    )
    return EconomicRow(
        plan_id=plan.id,
        strategy=plan.strategy,
        eligible=True,
        energy_kwh=metrics.energy_kwh,
        total_delay_s=metrics.total_delay_s,
        passenger_delay_s=metrics.passenger_delay_s,
        electricity_cost_tenge=electricity,
        passenger_delay_penalty_tenge=passenger,
        freight_delay_penalty_tenge=freight,
        generalized_cost_tenge=electricity + passenger + freight,
    )


def compare_plan_costs(
    scenario: Scenario,
    plans: list[Plan],
    reference_plan: Plan,
    rates: EconomicsRates,
    metric_config: MetricConfig,
    previous: Plan | None = None,
) -> EconomicComparison:
    """Rank supplied plans without running a new timetable optimization.

    Penalties apply to positive terminal lateness in train-hours. The reference
    must be valid in the current scenario and remains in the candidate set.
    Equal costs retain the reference, then preserve the input order.
    """
    candidates = [reference_plan]
    ids = {reference_plan.id}
    for plan in plans:
        if plan.id == reference_plan.id and plan == reference_plan:
            continue
        if plan.id in ids:
            raise ValueError(f"Duplicate plan ID: {plan.id}")
        ids.add(plan.id)
        candidates.append(plan)

    reference = _score(scenario, reference_plan, rates, metric_config, previous)
    if not reference.eligible:
        raise ValueError("Reference plan is not applicable: " + "; ".join(reference.reasons))
    reference_lateness = _passenger_lateness(scenario, reference_plan)
    rows = []
    for index, plan in enumerate(candidates):
        row = reference if index == 0 else _score(scenario, plan, rates, metric_config, previous)
        if row.generalized_cost_tenge is not None:
            lateness = _passenger_lateness(scenario, plan)
            extras = {
                train_id: max(0, value - reference_lateness[train_id])
                for train_id, value in lateness.items()
            }
            row.max_extra_passenger_delay_s = max(extras.values(), default=0)
            row.worsened_passenger_trains = [
                train_id for train_id, extra in extras.items() if extra
            ]
            for train_id, extra in extras.items():
                if extra > rates.max_extra_passenger_delay_s:
                    row.reasons.append(
                        f"PASSENGER_DELAY: {train_id} adds {extra}s; "
                        f"allowed {rates.max_extra_passenger_delay_s}s"
                    )
            if (
                rates.max_extra_total_delay_s is not None
                and row.total_delay_s > reference.total_delay_s + rates.max_extra_total_delay_s
            ):
                row.reasons.append(
                    f"TOTAL_DELAY: adds {row.total_delay_s - reference.total_delay_s}s; "
                    f"allowed {rates.max_extra_total_delay_s}s"
                )
            row.eligible = not row.reasons
            row.electricity_saving_tenge_vs_reference = (
                reference.electricity_cost_tenge - row.electricity_cost_tenge
            )
            row.generalized_saving_tenge_vs_reference = (
                reference.generalized_cost_tenge - row.generalized_cost_tenge
            )
        rows.append(row)
    selected = min(
        (row for row in rows if row.eligible), key=lambda row: row.generalized_cost_tenge
    )
    traffic = scenario.metadata.get("traffic", {})
    source = traffic.get("source", "unspecified") if isinstance(traffic, dict) else "unspecified"
    return EconomicComparison(
        scenario_id=scenario.id,
        state_version=scenario.state_version,
        traffic_source=str(source),
        rates=rates.model_copy(deep=True),
        operational_exactness=scenario.metadata.get("operational_exactness") is True,
        assumptions=list(scenario.metadata.get("assumptions", [])),
        reference_plan_id=reference_plan.id,
        selected_plan_id=selected.plan_id,
        rows=rows,
        caveats=[
            "Generalized cost is a model estimate, not measured operating expenditure or profit.",
            "Rates and delay penalties follow assumptions_note and input_status; no tariff is inferred.",
            "Energy already includes acceleration and auxiliary power during movement and waiting; "
            "do not add the same energy again as a stop or waiting charge.",
            "Delay penalties apply to train-hours of positive terminal lateness. "
            "They do not measure passenger-hours or actual crew and rolling-stock resource-hours.",
            "Energy inherits configured resistance and grades; physical profiles do not credit regeneration.",
        ],
    )
