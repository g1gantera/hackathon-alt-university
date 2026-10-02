"""Synthetic fixed blocks derived from the same speed envelope as the movement.

Times include tail release and headway. Opposing movements additionally lock
the complete line against one another. Block positions require operator data.
"""

import math
from functools import lru_cache

import numpy as np

from .advisory.speed import build_speed_profile, section_at_entry
from .schemas import Section, Train


@lru_cache(maxsize=4096)
def _offsets(train_json, section_json, origin, duration):
    train, section = (
        Train.model_validate_json(train_json),
        Section.model_validate_json(section_json),
    )
    hold = train.section_hold_s.get(section.id, 0)
    count = max(1, math.ceil(section.length_m / section.block_length_m))
    profile = build_speed_profile(
        train, section, duration - hold, reverse=origin == section.station_b
    )
    if profile.status != "FEASIBLE":
        raise ValueError("Block timing requires a feasible speed profile")
    times = [p.time_s for p in profile.points]
    positions = [p.position_m for p in profile.points]
    tail = section.headway_s + math.ceil(train.length_m / section.tail_clearance_speed_mps)
    result = []
    for i in range(count):
        a = i * section.length_m / count
        b = (i + 1) * section.length_m / count
        index = i if origin == section.station_a else count - 1 - i
        entry = 0 if i == 0 else hold + math.floor(float(np.interp(a, positions, times)))
        exit = hold + math.ceil(float(np.interp(b, positions, times))) + tail
        result.append((index, entry, exit))
    return tuple(result)


def offsets(train, section, origin, duration, entry=0):
    current = section_at_entry(train, section, entry)
    return _offsets(train.model_dump_json(), current.model_dump_json(), origin, duration)


def reservations(train, section, movement):
    if not section.block_length_m:
        return []
    return [
        (
            f"block:{section.id}:{movement.main_track_id}:{i}",
            movement.start_s + a,
            movement.start_s + b,
            train.id,
        )
        for i, a, b in offsets(
            train, section, movement.origin, movement.end_s - movement.start_s, movement.start_s
        )
    ]


def signal_states(scenario, plan, now, applicable=True):
    trains = {t.id: t for t in scenario.trains}
    result = []
    active = {
        m.section_id for m in plan.movements if m.start_s <= now + 900 and m.end_s + 300 >= now
    }
    for s in scenario.sections:
        if not s.block_length_m or s.id not in active:
            continue
        for track in s.main_tracks:
            reserved = (
                []
                if not applicable
                else [
                    r
                    for m in plan.movements
                    if m.section_id == s.id and m.main_track_id == track.id
                    for r in reservations(trains[m.train_id], s, m)
                ]
            )
            count = math.ceil(s.length_m / s.block_length_m)
            for origin in (s.station_a, s.station_b):
                if track.direction not in ("both", "a_to_b" if origin == s.station_a else "b_to_a"):
                    continue
                for i in range(1, count):
                    physical = i if origin == s.station_a else count - 1 - i
                    ids = [physical, physical + (1 if origin == s.station_a else -1)]
                    busy = [
                        any(
                            r[0] == f"block:{s.id}:{track.id}:{j}" and r[1] <= now < r[2]
                            for r in reserved
                        )
                        for j in ids
                    ]
                    restricted = any(
                        b.resource in (f"section:{s.id}", f"main_track:{s.id}:{track.id}")
                        and b.start_s <= now < b.end_s
                        for b in scenario.blocks
                    )
                    aspect = (
                        "red"
                        if not applicable or restricted or busy[0]
                        else "yellow"
                        if busy[1]
                        else "green"
                    )
                    result.append(
                        {
                            "id": f"block-signal:{s.id}:{track.id}:{origin}:{i}",
                            "type": "block",
                            "station_id": origin,
                            "section_id": s.id,
                            "main_track_id": track.id,
                            "offset_m": i * s.length_m / count,
                            "aspect": aspect,
                            "train_id": None,
                            "synthetic": True,
                            "reason": "Модельная автоблокировка: занятость следующего и последующего блоков; не натурная сигнализация",
                        }
                    )
    return result
