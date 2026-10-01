"""Public integration entry point. No background threads or implicit application of plans."""

import time

from app.metrics.quality import MetricConfig, Metrics, calculate_metrics, profile_plan
from app.planning.solver import solve_plan
from app.schemas import Model, Plan, Scenario, SpeedProfile


class Candidate(Model):
    plan: Plan
    metrics: Metrics
    profiles: dict[str, SpeedProfile]


class Alternatives(Model):
    state_version: int
    elapsed_ms: float
    budget_exceeded: bool
    candidates: list[Candidate]
    diagnostics: list[str]


def plan_alternatives(
    scenario: Scenario,
    config: MetricConfig,
    *,
    previous: Plan | None = None,
    time_budget_s: float = 5.0,
    strategies: tuple[str, ...] = ("balanced", "passenger"),
) -> Alternatives:
    if not strategies or len(set(strategies)) != len(strategies):
        raise ValueError("Supply nonempty unique strategies")
    if time_budget_s <= 0:
        raise ValueError("Budget must be positive")
    started = time.perf_counter()
    candidates, diagnostics = [], []
    for index, strategy in enumerate(strategies):
        remaining = time_budget_s - (time.perf_counter() - started)
        if remaining < 0.05:
            diagnostics.append(f"{strategy}: no time remains")
            break
        # Leave part of each allocation for physical profiles and metrics.
        # Long corridor profiles and their energy references need a material
        # share of the budget, even after vectorizing the physical envelope.
        solver_budget = 0.45 * remaining / (len(strategies) - index)
        result = solve_plan(
            scenario, strategy=strategy, previous=previous, time_budget_s=solver_budget
        )
        if result.plan is None:
            diagnostics.append(f"{strategy}: {result.status}; {'; '.join(result.diagnostics)}")
            continue
        profiles = profile_plan(scenario, result.plan)
        metrics = calculate_metrics(
            scenario, result.plan, config, previous=previous, profiles=profiles
        )
        if not metrics.applicable:
            diagnostics.append(f"{strategy}: rejected by profile or plan validation")
            continue
        candidates.append(Candidate(plan=result.plan, metrics=metrics, profiles=profiles))
    elapsed = (time.perf_counter() - started) * 1000
    return Alternatives(
        state_version=scenario.state_version,
        elapsed_ms=elapsed,
        budget_exceeded=elapsed > time_budget_s * 1000,
        candidates=candidates,
        diagnostics=diagnostics,
    )
