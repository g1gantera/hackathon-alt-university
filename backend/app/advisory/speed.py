"""Stop-to-stop speed envelope; Davis resistance and grades in energy, no regeneration.

Each spatial cell has constant acceleration. Speed limits are conservative at
boundaries. A lower cruise cap consumes available running-time slack. This is
an advisory heuristic, not a globally optimal train-control solution.
"""

import math
from functools import lru_cache

import numpy as np

from backend.app.schemas import Section, SpeedPoint, SpeedProfile, Train


def section_at_entry(train: Train, section: Section, entry_s: int) -> Section:
    factor = min(
        (
            limit.speed_factor
            for limit in section.entry_speed_limits
            if limit.start_s <= entry_s < limit.end_s
        ),
        default=1.0,
    )
    if factor == 1:
        return section
    return section.model_copy(
        update={
            "max_speed_mps": min(train.max_speed_mps, section.max_speed_mps) * factor,
            "entry_speed_limits": [],
        }
    )


def duration_windows(train: Train, section: Section, origin: str, horizon_s: int):
    """Partition entry times into constant speed regimes; adjacent equal times merge."""
    cuts = sorted(
        {
            0,
            horizon_s + 1,
            *(
                t
                for limit in section.entry_speed_limits
                for t in (limit.start_s, limit.end_s)
                if 0 < t <= horizon_s
            ),
        }
    )
    windows = []
    for start, end in zip(cuts, cuts[1:]):
        duration = minimum_duration_s(train, section_at_entry(train, section, start), origin)
        if windows and windows[-1][2] == duration:
            windows[-1] = (windows[-1][0], end, duration)
        else:
            windows.append((start, end, duration))
    return windows


def _grid(train: Train, section: Section, reverse: bool):
    limits = []
    for limit in section.speed_limits:
        a, b = limit.start_m, limit.end_m
        if reverse:
            a, b = section.length_m - b, section.length_m - a
        limits.append((a, b, limit.speed_mps))
    positions = np.unique(
        np.concatenate(
            (
                np.linspace(0, section.length_m, max(2, math.ceil(section.length_m / 25)) + 1),
                np.array([p for a, b, _ in limits for p in (a, b)]),
            )
        )
    )
    cap=min(train.max_speed_mps, section.max_speed_mps)
    if section.curve_radius_m is not None:
        cap=min(cap,math.sqrt(9.81*section.curve_radius_m*(section.cant_mm+100)/1520))
    ceilings = np.full(positions.shape, cap)
    for a, b, speed in limits:
        ceilings[(positions >= a) & (positions <= b)] = np.minimum(
            ceilings[(positions >= a) & (positions <= b)], speed
        )
    return positions, ceilings


def _envelope(x, limits, train, cap, initial_speed, final_speed):
    # In squared speeds the forward recurrence is
    # w[i] = min(limit[i]**2, w[i-1] + 2*a*dx).
    # Subtracting 2*a*x turns this into a prefix minimum. The backward
    # braking pass is the same recurrence in reverse. Both passes run in
    # NumPy, which matters when many long sections are evaluated repeatedly.
    ceiling_squared = np.asarray(limits) ** 2
    squared = ceiling_squared.copy()
    squared[0] = min(squared[0], initial_speed**2)
    acceleration_offset = 2 * train.acceleration_mps2 * (x - x[0])
    squared = acceleration_offset + np.minimum.accumulate(squared - acceleration_offset)
    # Remove round-off above the local limit before the braking pass.
    squared = np.clip(squared, 0, ceiling_squared)
    squared[-1] = min(squared[-1], final_speed**2)
    braking_offset = 2 * train.braking_mps2 * (x[-1] - x)
    braking_squared = braking_offset + np.minimum.accumulate((squared - braking_offset)[::-1])[::-1]
    speed = np.sqrt(np.clip(braking_squared, 0, squared))
    return _capped_envelope(x, speed, cap, initial_speed, final_speed)


def _capped_envelope(x, maximum_speed, cap, initial_speed, final_speed):
    # A constant cruise cap commutes with both acceleration/braking passes:
    # min(cap**2, w + 2*a*dx) is bounded by the local cap itself. Applying
    # it after the passes preserves the same feasible envelope while avoiding
    # cancellation at tiny caps. The duration search can reuse maximum_speed.
    speed = np.minimum(maximum_speed, cap)
    if abs(speed[0] - initial_speed) > 1e-7 or abs(speed[-1] - final_speed) > 1e-7:
        return None
    sums = speed[:-1] + speed[1:]
    if np.any(sums <= 0):
        return None
    dt = 2 * np.diff(x) / sums
    return speed, np.concatenate(([0.0], np.cumsum(dt)))


def build_speed_profile(
    train: Train,
    section: Section,
    target_duration_s: float | None = None,
    *,
    reverse: bool = False,
    initial_speed_mps: float = 0,
    final_speed_mps: float = 0,
) -> SpeedProfile:
    """Return a physically checked envelope, or UNREACHABLE; times are relative.

    For a complete route call once per movement and offset time and distance.
    target_duration_s excludes station dwell and resource clearance buffers.
    """
    if any(not math.isfinite(v) or v < 0 for v in (initial_speed_mps, final_speed_mps)):
        raise ValueError("Boundary speeds must be finite and nonnegative")
    if target_duration_s is not None and (
        not math.isfinite(target_duration_s) or target_duration_s <= 0
    ):
        raise ValueError("Target duration must be finite and positive")
    x, limits = _grid(train, section, reverse)
    cap = float(max(limits))
    if train.traction_power_w is not None:
        # Conservative constant acceleration based on the worst resistance at
        # the cruise cap. Power is mechanical power at the wheels.
        grade=section.grade_permille*(-1 if reverse else 1)
        for _ in range(100):
            if train.davis_resistance is None:
                resistance=train.mass_kg*9.81*train.rolling_coefficient+train.drag_n_per_mps2*cap**2
            else:
                c=train.davis_resistance;v=cap*3.6
                resistance=train.mass_kg*9.81/1000*(c.a+c.b*v+c.c*v*v)
            available=(train.traction_power_w/max(cap,0.01)-resistance-train.mass_kg*9.81*grade/1000)/train.mass_kg
            if available>0.005:break
            cap*=0.9
        limits=np.minimum(limits,cap)
        train=train.model_copy(update={'acceleration_mps2':max(0.001,min(train.acceleration_mps2,available)), 'braking_mps2':max(0.001,train.braking_mps2+9.81*grade/1000)})
    fastest = _envelope(x, limits, train, cap, initial_speed_mps, final_speed_mps)
    if fastest is None:
        return SpeedProfile(
            status="UNREACHABLE",
            minimum_duration_s=0,
            reason="Boundary speeds cannot satisfy acceleration/braking limits",
        )
    speed, times = fastest
    minimum = float(times[-1])
    if target_duration_s is not None:
        if target_duration_s < minimum - 1e-6:
            return SpeedProfile(
                status="UNREACHABLE",
                minimum_duration_s=minimum,
                reason="Requested arrival is earlier than physically reachable",
            )
        low, high = max(1e-5, initial_speed_mps, final_speed_mps), cap
        slowest = _capped_envelope(x, speed, low, initial_speed_mps, final_speed_mps)
        if slowest is None or float(slowest[1][-1]) < target_duration_s - 1e-6:
            return SpeedProfile(
                status="UNREACHABLE",
                minimum_duration_s=minimum,
                reason="Requested duration is outside this cruise-cap model",
            )
        for _ in range(55):
            middle = (low + high) / 2
            candidate = _capped_envelope(x, speed, middle, initial_speed_mps, final_speed_mps)
            if candidate is None:
                return SpeedProfile(
                    status="UNREACHABLE",
                    minimum_duration_s=minimum,
                    reason="No envelope for boundary speeds",
                )
            if candidate[1][-1] > target_duration_s:
                low = middle
            else:
                high = middle
        speed, times = _capped_envelope(x, speed, high, initial_speed_mps, final_speed_mps)
    from .energy import cell_energy
    gross,recovered=cell_energy(train,section,x,speed,times,reverse)
    traction_kwh=float(np.sum(gross))
    recovered_kwh=float(np.sum(recovered))
    auxiliary_kwh = train.auxiliary_power_w * float(times[-1]) / 3_600_000
    return SpeedProfile(
        status="FEASIBLE",
        duration_s=float(times[-1]),
        minimum_duration_s=minimum,
        traction_energy_kwh=traction_kwh,
        auxiliary_energy_kwh=auxiliary_kwh,
        regenerated_energy_kwh=recovered_kwh,
        energy_kwh=traction_kwh - recovered_kwh + auxiliary_kwh,
        points=[
            SpeedPoint(
                time_s=float(t), position_m=float(p), speed_mps=float(v), limit_mps=float(lim)
            )
            for t, p, v, lim in zip(times, x, speed, limits)
        ],
    )


@lru_cache(maxsize=1024)
def _minimum(train_json: str, section_json: str, reverse: bool) -> int:
    profile = build_speed_profile(
        Train.model_validate_json(train_json),
        Section.model_validate_json(section_json),
        reverse=reverse,
    )
    if profile.status != "FEASIBLE":
        raise ValueError(profile.reason)
    return math.ceil(profile.minimum_duration_s)


def minimum_duration_s(train: Train, section: Section, origin: str) -> int:
    # Timetable/identity/dispatch fields do not change a physical envelope.
    physical=train.model_copy(update={'id':'physics','route':['a','b'],'release_s':0,'due_s':0,'priority':1,'dispatch_category':None,'min_dwell_s':1,'not_before_s':{},'manual_station_tracks':{},'manual_main_tracks':{},'section_hold_s':{},'section_recovery_all_tracks':[]})
    geometry=section.model_copy(update={'id':'section','station_a':'a','station_b':'b','shared_resources':[],'entry_speed_limits':[],'main_tracks':[], 'block_length_m':0})
    # Keep a valid default track for schema validation inside the cache.
    from backend.app.schemas import MainTrack
    geometry.main_tracks=[MainTrack(id='1')]
    return _minimum(physical.model_dump_json(), geometry.model_dump_json(), origin == section.station_b)
