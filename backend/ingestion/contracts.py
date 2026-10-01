"""Versioned normalization boundary shared by embedded and HTTP deployments."""
import copy
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

KINDS = ('movement', 'infrastructure', 'timetable')
FIELDS = {'movement': ('trains',), 'infrastructure': ('sections', 'switches'),
          'timetable': ('plan', 'active_plan_id')}


class Frame(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    version: Literal[1] = 1
    kind: Literal['movement', 'infrastructure', 'timetable']
    epoch: str = Field(min_length=1, max_length=100)
    state_version: int = Field(ge=0, strict=True)
    sim_time_s: int = Field(ge=0, strict=True)
    payload: dict

    @model_validator(mode='after')
    def valid_payload(self):
        if set(self.payload) != set(FIELDS[self.kind]):
            raise ValueError('Unexpected payload fields')
        def number(value, *, nonnegative=False):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Telemetry must contain finite numbers')
            if nonnegative and value < 0:
                raise ValueError('Negative telemetry value')
        def unique(items):
            if not isinstance(items, list) or not items or len(items) > 10000:
                raise ValueError('Expected a bounded nonempty list')
            ids = [item.get('id') for item in items if isinstance(item, dict)]
            if len(ids) != len(items) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise ValueError('Missing or duplicate entity IDs')
        if self.kind == 'movement':
            unique(self.payload['trains'])
            for train in self.payload['trains']:
                for key in ('position_m', 'speed_mps', 'delay_s'):
                    number(train.get(key), nonnegative=True)
                coordinate = train.get('coordinate')
                if not isinstance(coordinate, list) or len(coordinate) != 2:
                    raise ValueError('Expected longitude/latitude')
                for component in coordinate:
                    number(component)
                if not (-180 <= coordinate[0] <= 180 and -90 <= coordinate[1] <= 90):
                    raise ValueError('Coordinate outside earth bounds')
                if type(train.get('direction')) is not int or train['direction'] not in (-1, 1) or train.get('status') not in ('moving', 'waiting', 'completed'):
                    raise ValueError('Unknown direction or train status')
        elif self.kind == 'infrastructure':
            unique(self.payload['sections']); unique(self.payload['switches'])
            for section in self.payload['sections']:
                if section.get('signal') not in ('red', 'green') or section.get('status') not in ('open', 'occupied', 'closed', 'signal_failure'):
                    raise ValueError('Unknown section or signal state')
            for switch in self.payload['switches']:
                if type(switch.get('available')) is not bool or switch.get('position') not in ('normal', 'route'):
                    raise ValueError('Unknown switch state')
        else:
            plan = self.payload['plan']
            active_id = self.payload['active_plan_id']
            if not isinstance(active_id, str) or not active_id or not isinstance(plan, dict) or plan.get('id') != active_id:
                raise ValueError('Plan identity mismatch')
            movements = plan.get('movements')
            if not isinstance(movements, list) or not 0 < len(movements) <= 100000:
                raise ValueError('Missing or oversized timetable')
            keys = set()
            for movement in movements:
                if not isinstance(movement, dict):
                    raise ValueError('Expected a timetable movement object')
                if not isinstance(movement.get('section_id'), str) or not movement['section_id']:
                    raise ValueError('Missing timetable section identity')
                for key in ('start_s', 'end_s', 'release_s'):
                    number(movement.get(key), nonnegative=True)
                if not movement['start_s'] < movement['end_s'] <= movement['release_s']:
                    raise ValueError('Invalid timetable interval')
                key = (movement.get('train_id'), movement.get('leg'))
                if not isinstance(key[0], str) or not key[0] or type(key[1]) is not int or key[1] < 0 or key in keys:
                    raise ValueError('Duplicate or invalid movement')
                keys.add(key)
        return self


def split(snapshot):
    identity = {key: snapshot[key] for key in ('epoch', 'state_version', 'sim_time_s')}
    return [Frame(kind=kind, **identity, payload={key: snapshot[key] for key in FIELDS[kind]}) for kind in KINDS]


def merge(snapshot, frames):
    if len(frames) != len(KINDS) or {frame.kind for frame in frames} != set(KINDS):
        raise ValueError('Incomplete ingestion batch')
    result = copy.deepcopy(snapshot)
    for frame in frames:
        if any(getattr(frame, key) != snapshot[key] for key in ('epoch', 'state_version', 'sim_time_s')):
            raise ValueError('Mixed epochs or revisions in ingestion batch')
        result.update(frame.payload)
    trains = {train['id'] for train in result['trains']}
    sections = {section['id'] for section in result['sections']}
    if any(m['train_id'] not in trains or m.get('section_id') not in sections for m in result['plan']['movements']):
        raise ValueError('Timetable references an unknown train or section')
    return result
