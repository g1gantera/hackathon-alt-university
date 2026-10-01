"""Forecast-only metrics; identical normalization for every candidate plan."""

import json
import math
from typing import Literal
from pathlib import Path

from pydantic import Field, model_validator

from backend.app.advisory.speed import build_speed_profile, section_at_entry
from backend.app.metrics.load import TrackLoadReport, calculate_track_load
from backend.app.schemas import Model, Plan, Scenario, SpeedPoint
from backend.app.validation.plan import validate_plan


class MetricConfig(Model):
    formula: Literal["weighted_sum", "weighted_geometric"] = "weighted_sum"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "punctuality": 0.30,
            "throughput": 0.20,
            "energy": 0.20,
            "conflicts": 0.15,
            "arrival_accuracy": 0.15,
        }
    )
    delay_scale_s: float = Field(default=3600, gt=0)
    arrival_tolerance_s: int = Field(default=60, ge=0)
    energy_budget_kwh: float = Field(default=8000, gt=0)
    normal_threshold: float = Field(default=80, ge=0, le=100)
    attention_threshold: float = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def valid(self):
        expected = {"punctuality", "throughput", "energy", "conflicts", "arrival_accuracy"}
        if set(self.weights) != expected or any(v < 0 for v in self.weights.values()):
            raise ValueError("Exactly five nonnegative metric weights required")
        if abs(sum(self.weights.values()) - 1) > 1e-6:
            raise ValueError("Metric weights must sum to one")
        if self.attention_threshold >= self.normal_threshold:
            raise ValueError("Thresholds must satisfy attention < normal")
        return self

    def score(self, components):
        known = {k: v for k, v in components.items() if v is not None and self.weights[k] > 0}
        total = sum(self.weights[k] for k in known)
        if not total:
            return None
        if self.formula == "weighted_geometric":
            value = 0 if any(v <= 0 for v in known.values()) else math.exp(
                sum(self.weights[k] * math.log(v) for k, v in known.items()) / total)
        else:
            value = sum(self.weights[k] * v for k, v in known.items()) / total
        return round(100 * value, 2)

    def category(self, score, applicable=True):
        if score is None:
            return "Нет данных"
        if not applicable or score < self.attention_threshold:
            return "Критично"
        return "Норма" if score >= self.normal_threshold else "Внимание"

    def contributions(self, components):
        total = sum(self.weights[k] for k, v in components.items() if v is not None)
        return {k: (100 * self.weights[k] * v / total
                    if v is not None and total and self.formula == "weighted_sum" else None)
                for k, v in components.items()}

    @classmethod
    def load(cls, path: str | Path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class Metrics(Model):
    kind: str = "forecast"
    total_delay_s: int
    weighted_delay_s: int
    passenger_delay_s: int
    max_delay_s: int
    on_time_fraction: float
    completed_by_window: int
    scheduled_by_window: int
    throughput_fraction: float
    conflict_count: int
    violation_count: int
    energy_kwh: float | None
    reference_energy_kwh: float | None
    energy_saving_pct: float | None
    components: dict[str, float | None]
    quality_index: float | None
    category: str
    applicable: bool
    calculation_ms: float
    config: dict
    track_load: TrackLoadReport | None = None
    data_quality: dict = Field(default_factory=dict)


def profile_plan(scenario: Scenario, plan: Plan):
    trains = {t.id: t for t in scenario.trains}
    sections = {s.id: s for s in scenario.sections}
    profiles = {}
    for m in plan.movements:
        train = trains[m.train_id]
        section = section_at_entry(train, sections[m.section_id], m.start_s)
        profile = build_speed_profile(
            train,
            section,
            m.end_s - m.start_s - m.hold_s,
            reverse=m.origin == sections[m.section_id].station_b,
        )
        if m.hold_s and profile.status == "FEASIBLE":
            # Recovery hold is stationary at the start of the reserved section.
            # It consumes auxiliary energy; it is not a fictitious slow traversal.
            for point in profile.points:
                point.time_s += m.hold_s
            first = profile.points[0]
            profile.points.insert(
                0, SpeedPoint(time_s=0, position_m=0, speed_mps=0, limit_mps=first.limit_mps)
            )
            profile.duration_s += m.hold_s
            profile.minimum_duration_s += m.hold_s
            profile.stationary_hold_s = m.hold_s
            auxiliary = train.auxiliary_power_w * m.hold_s / 3_600_000
            profile.auxiliary_energy_kwh += auxiliary
            profile.energy_kwh += auxiliary
        profiles[f"{m.train_id}:{m.section_id}"] = profile
    return profiles


def calculate_metrics(
    scenario: Scenario,
    plan: Plan,
    config: MetricConfig,
    *,
    previous: Plan | None = None,
    profiles: dict | None = None,
) -> Metrics:
    violations = validate_plan(scenario, plan, previous)
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    if any((t.id, t.route[-1]) not in stops for t in scenario.trains):
        raise ValueError("Cannot score a plan with missing terminal stops")
    delays = {t.id: max(0, stops[t.id, t.route[-1]].arrival_s - t.due_s) for t in scenario.trains}
    count = len(scenario.trains)
    weighted = sum(t.priority * delays[t.id] for t in scenario.trains)
    mean_weighted = weighted / sum(t.priority for t in scenario.trains)
    on_time = (
        sum(
            abs(stops[t.id, t.route[-1]].arrival_s - t.due_s) <= config.arrival_tolerance_s
            for t in scenario.trains
        )
        / count
    )
    eligible = [t for t in scenario.trains if t.due_s <= scenario.evaluation_end_s]
    completed = sum(
        stops[t.id, t.route[-1]].arrival_s <= scenario.evaluation_end_s for t in eligible
    )
    throughput = completed / len(eligible) if eligible else 1.0
    energy, reference = None, None
    if not violations:
        profiles = profile_plan(scenario, plan) if profiles is None else profiles
        required = {f"{m.train_id}:{m.section_id}" for m in plan.movements}
        if set(profiles) != required:
            raise ValueError("Profiles must cover every movement exactly")
        if all(p.status == "FEASIBLE" for p in profiles.values()):
            energy = sum(p.energy_kwh for p in profiles.values())
            trains = {t.id: t for t in scenario.trains}
            sections = {s.id: s for s in scenario.sections}
            reference = sum(
                build_speed_profile(
                    trains[m.train_id],
                    section_at_entry(trains[m.train_id], sections[m.section_id], m.start_s),
                    reverse=m.origin == sections[m.section_id].station_b,
                ).energy_kwh
                for m in plan.movements
            )
            # Include auxiliary energy while waiting off-network and on station tracks.
            # Reference has minimum station dwell and no off-network queue; explicit in docs.
            for train in scenario.trains:
                train_stops = [stops[train.id, s] for s in train.route]
                wait = sum(s.departure_s - s.arrival_s for s in train_stops)
                wait += train_stops[0].arrival_s - train.release_s
                energy += train.auxiliary_power_w * wait / 3_600_000
                reference += (
                    train.auxiliary_power_w * len(train.route) * train.min_dwell_s / 3_600_000
                )
    conflict_count = sum(v.code == "RESOURCE_CONFLICT" for v in violations)
    applicable = not violations and energy is not None
    components = {
        "punctuality": max(0.0, 1 - mean_weighted / config.delay_scale_s),
        "throughput": throughput,
        "energy": None if energy is None else max(0.0, 1 - energy / config.energy_budget_kwh),
        "conflicts": 0.0 if violations else 1.0,
        "arrival_accuracy": on_time,
    }
    quality = config.score(components) if all(v is not None for v in components.values()) else None
    category = config.category(quality, applicable) if applicable else "Критично"
    return Metrics(
        kind="synthetic_forecast"
        if scenario.metadata.get("traffic", {}).get("source") == "synthetic"
        else "forecast",
        total_delay_s=sum(delays.values()),
        weighted_delay_s=weighted,
        passenger_delay_s=sum(delays[t.id] for t in scenario.trains if t.kind == "passenger"),
        max_delay_s=max(delays.values()),
        on_time_fraction=on_time,
        completed_by_window=completed,
        scheduled_by_window=len(eligible),
        throughput_fraction=throughput,
        conflict_count=conflict_count,
        violation_count=len(violations),
        energy_kwh=energy,
        reference_energy_kwh=reference,
        energy_saving_pct=None
        if energy is None or not reference
        else 100 * (1 - energy / reference),
        components=components,
        quality_index=quality,
        category=category,
        applicable=applicable,
        calculation_ms=plan.elapsed_ms,
        config=json.loads(config.model_dump_json()),
        track_load=calculate_track_load(scenario, plan, window_start_s=0, previous=previous)
        if applicable
        else None,
        data_quality={
            "operational_exactness": scenario.metadata.get("operational_exactness"),
            "assumptions": scenario.metadata.get("assumptions", []),
        },
    )
