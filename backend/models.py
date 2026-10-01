"""Validated public commands and persisted configuration (SI units unless named)."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Stop(Strict):
    vertex: int = Field(ge=0)
    dwell_s: float = Field(default=30, ge=0, le=7200)
    scheduled_arrival_s: float | None = Field(default=None, ge=0)


class TrainSpec(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,24}$')
    name: str = Field(min_length=1, max_length=80)
    origin: int = Field(ge=0)
    destination: int = Field(ge=0)
    departure_s: float = Field(default=0, ge=0)
    scheduled_arrival_s: float | None = Field(default=None, ge=0)
    importance: int = Field(default=5, ge=1, le=10)
    length_m: float = Field(default=180, ge=10, le=2000)
    max_speed_kmh: float = Field(default=80, ge=5, le=160)
    acceleration_mps2: float = Field(default=.45, ge=.05, le=1.5)
    braking_mps2: float = Field(default=.7, ge=.1, le=2)
    mass_t: float = Field(default=400, ge=20, le=10000)
    stops: list[Stop] = Field(default_factory=list, max_length=20)
    via_siding: int | None = Field(default=None, ge=0)
    give_way_s: float = Field(default=180, ge=0, le=3600)

    @model_validator(mode='after')
    def ordering(self):
        if self.origin == self.destination:
            raise ValueError('Origin and destination must differ')
        times = [self.departure_s] + [s.scheduled_arrival_s for s in self.stops if s.scheduled_arrival_s is not None]
        if self.scheduled_arrival_s is not None:
            times.append(self.scheduled_arrival_s)
        if times != sorted(times):
            raise ValueError('Arrival targets must be in chronological order after departure')
        if self.via_siding is not None and self.stops:
            raise ValueError('Use station stops or a siding holding point in one journey, not both')
        return self


class TrainUpdate(Strict):
    importance: int | None = Field(default=None, ge=1, le=10)
    scheduled_arrival_s: float | None = Field(default=None, ge=0)


class IncidentSpec(Strict):
    id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,48}$')
    kind: Literal['train_delay','train_breakdown','signal_failure','track_closure','speed_restriction']
    asset_type: Literal['train','signal','station','switch','edge']
    asset_id: str = Field(min_length=1, max_length=48)
    start_s: float | None = Field(default=None, ge=0)
    duration_s: float | None = Field(default=120, ge=1, le=86400)
    speed_kmh: float = Field(default=20, ge=5, le=160)
    note: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def asset_matches(self):
        valid = {'train_delay':{'train','station'},'train_breakdown':{'train'},'signal_failure':{'signal','switch'},'track_closure':{'edge','station','switch'},'speed_restriction':{'edge'}}
        if self.asset_type not in valid[self.kind]:
            raise ValueError('Incident type is not appropriate for this asset type')
        return self


class Weights(Strict):
    importance: float = Field(default=4, ge=0, le=20)
    delay: float = Field(default=2, ge=0, le=20)
    waiting: float = Field(default=3, ge=.1, le=20)
    energy: float = Field(default=1, ge=0, le=20)


class Settings(Strict):
    weights: Weights = Field(default_factory=Weights)
    normal_threshold: float = Field(default=80, ge=0, le=100)
    attention_threshold: float = Field(default=55, ge=0, le=100)
    simulation_speed: float = Field(default=30, ge=.1, le=120)
    clearance_m: float = Field(default=25, ge=10, le=100)
    # Assumed signalling overlay; these values are not surveyed KTZ assets.
    signal_block_m: float = Field(default=2000, ge=500, le=5000)
    authority_lookahead_m: float = Field(default=5000, ge=1000, le=20000)
    starvation_s: float = Field(default=300, ge=30, le=3600)
    retention_hours: int = Field(default=48, ge=24, le=72)
    quality_window_s: int = Field(default=300, ge=60, le=900)
    random_incidents_per_hour: float = Field(default=0, ge=0, le=120)
    random_seed: int = Field(default=42, ge=0, le=2**31-1)
    terminal_release_s: float = Field(default=30, ge=1, le=600)
    # Automatic meets at passing loops, receiving-track sections and planned
    # overtakes. Off: each train commits its whole leg to the next stop.
    auto_dispatch: bool = True

    @model_validator(mode='after')
    def thresholds(self):
        if self.attention_threshold >= self.normal_threshold:
            raise ValueError('Attention threshold must be below Normal')
        return self


class Control(Strict):
    action: Literal['start','pause','resume','reset','speed']
    speed: float | None = Field(default=None, ge=.1, le=120)


class DemoCommand(Strict):
    scenario: Literal['passing','overtaking','priority','closure','empty'] = 'passing'


class IngestEvent(Strict):
    source: str = Field(pattern=r'^[A-Za-z0-9_-]{1,32}$')
    sequence: int = Field(ge=0)
    sim_time: float = Field(ge=0)
    incident: IncidentSpec
