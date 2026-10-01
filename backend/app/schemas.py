"""Public JSON contracts. SI units, integer simulation seconds, half-open reservations."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Track(Model):
    id: str = Field(min_length=1)
    length_m: float = Field(gt=0)


class Switch(Model):
    """Conservative synthetic station throat; all routes share this lock."""

    id: str = "throat"
    clearance_s: int = Field(default=15, ge=1, le=300)


class Station(Model):
    id: str = Field(min_length=1)
    name: str
    tracks: list[Track] = Field(min_length=1)
    clearance_s: int = Field(default=10, ge=1)
    switch: Switch | None = None


class SpeedLimit(Model):
    start_m: float = Field(ge=0)
    end_m: float = Field(gt=0)
    speed_mps: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_m <= self.start_m:
            raise ValueError("Speed limit end must exceed start")
        return self


class MainTrack(Model):
    """A distinct main line between stations, with an explicit operating direction.

    Direction is relative to Section.station_a / station_b. Numbering and
    direction must be recorded as sourced data or assumptions in metadata.
    """

    id: str = Field(min_length=1)
    direction: Literal["both", "a_to_b", "b_to_a"] = "both"


class EntrySpeedLimit(Model):
    """Railsim semantics: the speed factor is sampled when a train enters."""

    id: str
    start_s: int = Field(ge=0)
    end_s: int = Field(gt=0)
    speed_factor: float = Field(gt=0, le=1)
    reason: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("Entry speed limit end must exceed start")
        return self


class DavisResistance(Model):
    """Specific resistance A+B*v+C*v² in N/kN, with v in km/h, from railsim."""

    a: float = Field(ge=0)
    b: float = Field(ge=0)
    c: float = Field(ge=0)


class Section(Model):
    id: str = Field(min_length=1)
    station_a: str
    station_b: str
    length_m: float = Field(gt=0)
    max_speed_mps: float = Field(gt=0)
    main_tracks: list[MainTrack] = Field(default_factory=lambda: [MainTrack(id="1")], min_length=1)
    headway_s: int = Field(default=10, ge=0)
    # Conservative tail-release speed, NOT actual terminal speed (which is zero).
    tail_clearance_speed_mps: float = Field(default=5, gt=0)
    speed_limits: list[SpeedLimit] = Field(default_factory=list)
    entry_speed_limits: list[EntrySpeedLimit] = Field(default_factory=list)
    grade_permille: float = Field(default=0, ge=-60, le=60)
    # A switch/junction resource is reserved for the entire traversal in this MVP.
    shared_resources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid(self):
        if self.station_a == self.station_b:
            raise ValueError("Section endpoints must differ")
        if len({track.id for track in self.main_tracks}) != len(self.main_tracks):
            raise ValueError("Duplicate main track ID")
        if any(limit.end_m > self.length_m for limit in self.speed_limits):
            raise ValueError("Speed limit lies outside section")
        if len(set(self.shared_resources)) != len(self.shared_resources):
            raise ValueError("Duplicate shared resource")
        if len({limit.id for limit in self.entry_speed_limits}) != len(self.entry_speed_limits):
            raise ValueError("Duplicate entry speed limit ID")
        if any(not r.startswith("junction:") for r in self.shared_resources):
            raise ValueError("Shared resource must start with junction:")
        return self


class Train(Model):
    id: str = Field(min_length=1)
    kind: Literal["passenger", "freight"]
    dispatch_category: Literal["emergency", "passenger", "express_freight", "freight", "service"] | None = None
    priority: int = Field(default=1, ge=1, le=100)
    route: list[str] = Field(min_length=2)
    release_s: int = Field(ge=0)
    due_s: int = Field(ge=0)
    min_dwell_s: int = Field(default=30, ge=1)
    length_m: float = Field(gt=0)
    mass_kg: float = Field(gt=0)
    max_speed_mps: float = Field(gt=0)
    acceleration_mps2: float = Field(default=0.4, gt=0)
    braking_mps2: float = Field(default=0.5, gt=0)
    traction_efficiency: float = Field(default=0.88, gt=0, le=1)
    rolling_coefficient: float = Field(default=0.0015, ge=0)
    drag_n_per_mps2: float = Field(default=5, ge=0)
    auxiliary_power_w: float = Field(default=10000, ge=0)
    davis_resistance: DavisResistance | None = None
    # Known stationary hold within a future section; the path remains occupied.
    section_hold_s: dict[str, int] = Field(default_factory=dict)
    section_recovery_all_tracks: list[str] = Field(default_factory=list)
    manual_station_tracks: dict[str, str] = Field(default_factory=dict)
    manual_main_tracks: dict[str, str] = Field(default_factory=dict)
    # Earliest permitted departure at a route station, e.g. after a train delay.
    not_before_s: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.route)) != len(self.route):
            raise ValueError("MVP supports routes without repeated stations")
        if self.due_s < self.release_s:
            raise ValueError("due_s must not precede release_s")
        if any(k not in self.route or v < 0 for k, v in self.not_before_s.items()):
            raise ValueError("Invalid departure delay")
        if any(v < 0 for v in self.section_hold_s.values()):
            raise ValueError("Section hold must be nonnegative")
        if any(not self.section_hold_s.get(s) for s in self.section_recovery_all_tracks):
            raise ValueError("Whole-section recovery requires a positive section hold")
        return self


class Block(Model):
    id: str
    resource: str
    start_s: int = Field(ge=0)
    end_s: int = Field(gt=0)
    kind: Literal["closure", "signal"] = "closure"

    @model_validator(mode="after")
    def valid(self):
        if self.end_s <= self.start_s:
            raise ValueError("Block end must exceed start")
        if self.kind == "signal" and not self.resource.startswith(("section:", "main_track:")):
            raise ValueError("Signal blocks apply to section or main-track entry")
        return self


class Scenario(Model):
    id: str
    state_version: int = Field(default=1, ge=0)
    now_s: int = Field(default=0, ge=0)
    horizon_s: int = Field(default=14400, gt=0)
    evaluation_end_s: int = Field(default=7200, gt=0)
    stations: list[Station] = Field(min_length=2)
    sections: list[Section] = Field(min_length=1)
    trains: list[Train] = Field(min_length=1)
    blocks: list[Block] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        if self.now_s >= self.horizon_s or self.evaluation_end_s > self.horizon_s:
            raise ValueError("Invalid planning/evaluation horizon")
        for objects in (self.stations, self.sections, self.trains, self.blocks):
            if len({obj.id for obj in objects}) != len(objects):
                raise ValueError("Duplicate object ID")
        stations = {s.id: s for s in self.stations}
        pairs = set()
        resources = set()
        for station in self.stations:
            if len({t.id for t in station.tracks}) != len(station.tracks):
                raise ValueError("Duplicate track ID")
            resources.update(f"track:{station.id}:{t.id}" for t in station.tracks)
            if station.switch:
                resources.add(f"switch:{station.id}:{station.switch.id}")
        for section in self.sections:
            if section.station_a not in stations or section.station_b not in stations:
                raise ValueError("Unknown section endpoint")
            pair = frozenset((section.station_a, section.station_b))
            if pair in pairs:
                raise ValueError("MVP supports one section per pair of stations")
            pairs.add(pair)
            resources.add(f"section:{section.id}")
            resources.update(f"main_track:{section.id}:{t.id}" for t in section.main_tracks)
            resources.update(section.shared_resources)
        for train in self.trains:
            if any(s not in stations for s in train.route):
                raise ValueError("Unknown route station")
            for a, b in zip(train.route, train.route[1:]):
                if frozenset((a, b)) not in pairs:
                    raise ValueError("Disconnected route")
            route_sections = {
                s.id
                for s in self.sections
                if any(
                    {s.station_a, s.station_b} == {a, b}
                    for a, b in zip(train.route, train.route[1:])
                )
            }
            for sid, tid in train.manual_station_tracks.items():
                if sid not in train.route or not any(t.id == tid and t.length_m >= train.length_m for t in stations[sid].tracks):
                    raise ValueError("Invalid manual station track")
            for sid, tid in train.manual_main_tracks.items():
                section = next((s for s in self.sections if s.id == sid), None)
                if sid not in route_sections or section is None:
                    raise ValueError("Manual main track lies outside route")
                direction = "a_to_b" if train.route.index(section.station_a) < train.route.index(section.station_b) else "b_to_a"
                if not any(t.id == tid and t.direction in ("both", direction) for t in section.main_tracks):
                    raise ValueError("Manual main track does not permit this direction")
            if any(s not in route_sections for s in train.section_hold_s):
                raise ValueError("Section hold lies outside the train route")
            if any(
                not any(t.length_m >= train.length_m for t in stations[s].tracks)
                for s in train.route
            ):
                raise ValueError("Train does not fit any track at a route station")
        if any(b.resource not in resources for b in self.blocks):
            raise ValueError("Unknown blocked resource")
        return self


class Stop(Model):
    train_id: str
    station_id: str
    track_id: str
    arrival_s: int = Field(ge=0)
    departure_s: int = Field(ge=0)


class Movement(Model):
    train_id: str
    section_id: str
    main_track_id: str = "1"
    origin: str
    destination: str
    start_s: int = Field(ge=0)
    end_s: int = Field(ge=0)
    hold_s: int = Field(default=0, ge=0)


class Plan(Model):
    id: str
    scenario_id: str
    state_version: int
    strategy: Literal["baseline", "balanced", "passenger", "eco"]
    solver_status: Literal["FEASIBLE", "OPTIMAL"]
    stops: list[Stop]
    movements: list[Movement]
    objective_value: float | None = None
    elapsed_ms: float = 0
    explanations: list[str] = Field(default_factory=list)


class Violation(Model):
    code: str
    message: str
    train_ids: list[str] = Field(default_factory=list)
    resource: str | None = None


class PlanResult(Model):
    status: Literal["FEASIBLE", "OPTIMAL", "INFEASIBLE", "UNKNOWN", "INVALID"]
    plan: Plan | None = None
    elapsed_ms: float
    diagnostics: list[str] = Field(default_factory=list)


class SpeedPoint(Model):
    time_s: float
    position_m: float
    speed_mps: float
    limit_mps: float


class SpeedProfile(Model):
    status: Literal["FEASIBLE", "UNREACHABLE"]
    duration_s: float | None = None
    minimum_duration_s: float
    traction_energy_kwh: float | None = None
    auxiliary_energy_kwh: float | None = None
    energy_kwh: float | None = None
    points: list[SpeedPoint] = Field(default_factory=list)
    reason: str | None = None
    stationary_hold_s: int = Field(default=0, ge=0)
