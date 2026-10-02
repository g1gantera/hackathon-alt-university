"""Forecast reservation load; never a measurement of actual railway traffic."""

import math
from collections import defaultdict
from typing import Any, Literal

from pydantic import Field

from backend.app.planning.common import clearance_s
from backend.app.schemas import Model, Plan, Scenario
from backend.app.validation.plan import validate_plan


class TrackLoad(Model):
    resource_id: str
    resource_type: Literal["main_track", "station_track"]
    label: str
    track_id: str
    section_id: str | None = None
    station_id: str | None = None
    direction: Literal["both", "a_to_b", "b_to_a"] | None = None
    occupied_s: int = Field(ge=0)
    closed_s: int = Field(ge=0)
    available_s: int = Field(ge=0)
    entry_blocked_s: int = Field(ge=0)
    occupancy_fraction: float = Field(ge=0, le=1)
    utilization_of_available: float | None = Field(default=None, ge=0, le=1)
    train_entries: int = Field(ge=0)
    counts_a_to_b: int | None = Field(default=None, ge=0)
    counts_b_to_a: int | None = Field(default=None, ge=0)
    mapping_status: str = "unspecified"
    mapping_evidence: dict[str, Any] = Field(default_factory=dict)


class TrackLoadReport(Model):
    kind: Literal["forecast", "synthetic_forecast"]
    scenario_id: str
    state_version: int
    plan_id: str
    window_start_s: int
    window_end_s: int
    occupation_basis: Literal["reservations_including_clearance"] = (
        "reservations_including_clearance"
    )
    tracks: list[TrackLoad]


def _union_seconds(intervals, window_start_s, window_end_s):
    clipped = sorted(
        (max(start, window_start_s), min(end, window_end_s))
        for start, end in intervals
        if start < window_end_s and window_start_s < end
    )
    total, cursor = 0, window_start_s
    for start, end in clipped:
        total += max(0, end - max(start, cursor))
        cursor = max(cursor, end)
    return total


def _mapping(scenario, resource, parent):
    evidence = scenario.metadata.get("mapping_evidence", {})
    if not isinstance(evidence, dict):
        return "unspecified", {}
    value = evidence.get(resource, evidence.get(parent, {}))
    if isinstance(value, str):
        return value, {"status": value}
    if isinstance(value, dict):
        return str(value.get("status", "unspecified")), value
    return "unspecified", {}


def calculate_track_load(
    scenario: Scenario,
    plan: Plan,
    window_start_s: int | None = None,
    window_end_s: int | None = None,
    previous: Plan | None = None,
) -> TrackLoadReport:
    """Count half-open reservations and entries in a fixed simulation window.

    Occupation includes tail clearance and headway margins. Signals prohibit
    entry; they do not remove physical capacity from available seconds.
    A plan that fails independent validation is rejected before aggregation.
    """
    start = scenario.now_s if window_start_s is None else window_start_s
    end = scenario.evaluation_end_s if window_end_s is None else window_end_s
    if (
        any(
            not isinstance(t, int) or isinstance(t, bool) or not math.isfinite(t)
            for t in (start, end)
        )
        or not 0 <= start < end <= scenario.horizon_s
    ):
        raise ValueError("Load window must have integer bounds: 0 <= start < end <= horizon")
    violations = validate_plan(scenario, plan, previous)
    if violations:
        codes = ", ".join(sorted({v.code for v in violations}))
        raise ValueError(f"Cannot calculate load for invalid plan: {codes}")

    stations = {s.id: s for s in scenario.stations}
    sections = {s.id: s for s in scenario.sections}
    trains = {t.id: t for t in scenario.trains}
    resources = {}
    for station in scenario.stations:
        for track in station.tracks:
            resource = f"track:{station.id}:{track.id}"
            resources[resource] = {
                "resource_type": "station_track",
                "track_id": track.id,
                "station_id": station.id,
                "label": f"{station.name} · путь {track.id}",
                "parent": f"station:{station.id}",
            }
    for section in scenario.sections:
        for track in section.main_tracks:
            resource = f"main_track:{section.id}:{track.id}"
            resources[resource] = {
                "resource_type": "main_track",
                "track_id": track.id,
                "section_id": section.id,
                "direction": track.direction,
                "label": (
                    f"{stations[section.station_a].name} — "
                    f"{stations[section.station_b].name} · главный путь {track.id}"
                ),
                "parent": f"section:{section.id}",
            }

    occupied, closures, signals = defaultdict(list), defaultdict(list), defaultdict(list)
    entries, forward, reverse = defaultdict(int), defaultdict(int), defaultdict(int)
    for stop in plan.stops:
        resource = f"track:{stop.station_id}:{stop.track_id}"
        occupied[resource].append(
            (stop.arrival_s, stop.departure_s + stations[stop.station_id].clearance_s)
        )
        entries[resource] += int(start <= stop.arrival_s < end)
    for movement in plan.movements:
        section = sections[movement.section_id]
        resource = f"main_track:{section.id}:{movement.main_track_id}"
        occupied[resource].append(
            (movement.start_s, movement.end_s + clearance_s(trains[movement.train_id], section))
        )
        entered = int(start <= movement.start_s < end)
        entries[resource] += entered
        counts = forward if movement.origin == section.station_a else reverse
        counts[resource] += entered
        if section.id in trains[movement.train_id].section_recovery_all_tracks:
            for track in section.main_tracks:
                if track.id != movement.main_track_id:
                    closures[f"main_track:{section.id}:{track.id}"].append(
                        (movement.start_s, movement.start_s + movement.hold_s)
                    )
    for block in scenario.blocks:
        targets = [block.resource]
        if block.resource.startswith("section:"):
            section = sections[block.resource.removeprefix("section:")]
            targets = [f"main_track:{section.id}:{t.id}" for t in section.main_tracks]
        for resource in targets:
            if resource in resources:
                (closures if block.kind == "closure" else signals)[resource].append(
                    (block.start_s, block.end_s)
                )

    results = []
    window = end - start
    for resource, properties in resources.items():
        properties = dict(properties)
        mapping_status, evidence = _mapping(scenario, resource, properties.pop("parent"))
        occupied_s = _union_seconds(occupied[resource], start, end)
        closed_s = _union_seconds(closures[resource], start, end)
        available_s = window - closed_s
        main = properties["resource_type"] == "main_track"
        results.append(
            TrackLoad(
                resource_id=resource,
                **properties,
                occupied_s=occupied_s,
                closed_s=closed_s,
                available_s=available_s,
                entry_blocked_s=_union_seconds(signals[resource], start, end),
                occupancy_fraction=occupied_s / window,
                utilization_of_available=occupied_s / available_s if available_s else None,
                train_entries=entries[resource],
                counts_a_to_b=forward[resource] if main else None,
                counts_b_to_a=reverse[resource] if main else None,
                mapping_status=mapping_status,
                mapping_evidence=evidence,
            )
        )
    traffic = scenario.metadata.get("traffic", {})
    synthetic = isinstance(traffic, dict) and traffic.get("source") == "synthetic"
    return TrackLoadReport(
        kind="synthetic_forecast" if synthetic else "forecast",
        scenario_id=scenario.id,
        state_version=scenario.state_version,
        plan_id=plan.id,
        window_start_s=start,
        window_end_s=end,
        tracks=results,
    )
