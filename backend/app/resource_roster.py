"""Fixed resource rotations: explicit availability, geography and duty windows."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResourceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1)
    kind: Literal["locomotive", "wagons", "crew"]
    initial_station: str
    available_s: int = Field(default=0, ge=0)
    unavailable_s: int = Field(gt=0)
    turnaround_s: int = Field(default=0, ge=0)
    train_ids: list[str] = Field(min_length=1)
    max_mass_kg: float | None = Field(default=None, gt=0)
    max_length_m: float | None = Field(default=None, gt=0)


class ResourceRoster(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    units: list[ResourceUnit]


def roster_for(scenario):
    raw = scenario.metadata.get("resource_roster")
    return None if raw is None else ResourceRoster.model_validate(raw)


def check_roster(scenario):
    roster = roster_for(scenario)
    if roster is None:
        return
    trains = {t.id: t for t in scenario.trains}
    stations = {s.id for s in scenario.stations}
    seen_ids, covered = set(), set()
    for unit in roster.units:
        if unit.id in seen_ids:
            raise ValueError("Duplicate resource ID")
        seen_ids.add(unit.id)
        if unit.available_s >= unit.unavailable_s or unit.initial_station not in stations:
            raise ValueError("Invalid resource availability or initial station")
        location = unit.initial_station
        for tid in unit.train_ids:
            if tid not in trains or (unit.kind, tid) in covered:
                raise ValueError("Unknown or multiply assigned train in resource roster")
            covered.add((unit.kind, tid))
            train = trains[tid]
            if train.route[0] != location:
                raise ValueError("Resource rotation requires a scheduled transfer between stations")
            if unit.max_mass_kg is not None and train.mass_kg > unit.max_mass_kg:
                raise ValueError("Train exceeds resource mass capacity")
            if unit.max_length_m is not None and train.length_m > unit.max_length_m:
                raise ValueError("Train exceeds resource length capacity")
            location = train.route[-1]
    expected = {(kind, tid) for kind in ("locomotive", "wagons", "crew") for tid in trains}
    if covered != expected:
        raise ValueError(
            "Every train needs exactly one locomotive, wagon consist and crew assignment"
        )


def constrain_roster(model, scenario, stop_vars):
    roster = roster_for(scenario)
    if roster is None:
        return
    trains = {t.id: t for t in scenario.trains}
    for unit in roster.units:
        ready = unit.available_s
        for tid in unit.train_ids:
            train = trains[tid]
            start = stop_vars[tid, train.route[0]][0]
            end = stop_vars[tid, train.route[-1]][1]
            model.add(start >= ready)
            model.add(end <= unit.unavailable_s)
            ready = end + unit.turnaround_s


def validate_roster(scenario, plan):
    from .schemas import Violation

    try:
        check_roster(scenario)
        roster = roster_for(scenario)
    except ValueError as error:
        return [Violation(code="RESOURCE_ROSTER", message=str(error), train_ids=[])]
    if roster is None:
        return []
    trains = {t.id: t for t in scenario.trains}
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    errors = []
    for unit in roster.units:
        ready = unit.available_s
        for tid in unit.train_ids:
            train = trains[tid]
            start = stops[tid, train.route[0]].arrival_s
            end = stops[tid, train.route[-1]].departure_s
            if start < ready or end > unit.unavailable_s:
                errors.append(
                    Violation(
                        code="RESOURCE_AVAILABILITY",
                        message=f"{unit.id}: assigned turn violates availability, turnaround or duty window",
                        train_ids=[tid],
                        resource=f"fleet:{unit.id}",
                    )
                )
            ready = end + unit.turnaround_s
    return errors


def model_roster(scenario, plan):
    """Editable synthetic inventory, one unit of each type per trip; no claim of KTZ stock."""
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    units = []
    for train in scenario.trains:
        start = stops[train.id, train.route[0]].arrival_s
        for kind in ("locomotive", "wagons", "crew"):
            units.append(
                ResourceUnit(
                    id=f"MODEL-{kind}-{train.id}",
                    kind=kind,
                    initial_station=train.route[0],
                    available_s=start,
                    unavailable_s=min(scenario.horizon_s, start + 8 * 3600)
                    if kind == "crew"
                    else scenario.horizon_s,
                    turnaround_s=1800 if kind != "crew" else 300,
                    train_ids=[train.id],
                )
            )
    return ResourceRoster(
        source="hackathon_assumption: separate units per train, crew duty 8h", units=units
    )
