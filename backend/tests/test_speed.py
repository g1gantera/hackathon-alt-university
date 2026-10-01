import math

import pytest

from app.advisory.speed import build_speed_profile
from app.schemas import SpeedLimit


def test_profile_respects_physics_and_arrival(small):
    train, section = small.trains[0], small.sections[0]
    section.speed_limits = [SpeedLimit(start_m=300, end_m=550, speed_mps=8)]
    fastest = build_speed_profile(train, section)
    target = math.ceil(fastest.minimum_duration_s * 1.25)
    profile = build_speed_profile(train, section, target)
    assert profile.status == "FEASIBLE"
    assert profile.duration_s == pytest.approx(target, abs=1e-5)
    assert profile.points[0].speed_mps == profile.points[-1].speed_mps == 0
    assert profile.points[-1].position_m == section.length_m
    for a, b in zip(profile.points, profile.points[1:]):
        assert b.time_s > a.time_s
        assert b.position_m > a.position_m
        assert b.speed_mps <= b.limit_mps + 1e-8
        acceleration = (b.speed_mps**2 - a.speed_mps**2) / (2 * (b.position_m - a.position_m))
        assert -train.braking_mps2 - 1e-8 <= acceleration <= train.acceleration_mps2 + 1e-8
    assert profile.energy_kwh > 0
    assert profile.energy_kwh == pytest.approx(
        profile.traction_energy_kwh + profile.auxiliary_energy_kwh
    )


def test_too_early_arrival_is_explicitly_unreachable(small):
    profile = build_speed_profile(small.trains[0], small.sections[0], 1)
    assert profile.status == "UNREACHABLE"
    assert not profile.points and profile.energy_kwh is None


def test_invalid_initial_speed_cannot_be_silently_lowered(small):
    profile = build_speed_profile(small.trains[0], small.sections[0], initial_speed_mps=40)
    assert profile.status == "UNREACHABLE"


def test_reverse_direction_mirrors_speed_limit(small):
    section = small.sections[0]
    section.speed_limits = [SpeedLimit(start_m=100, end_m=200, speed_mps=5)]
    profile = build_speed_profile(small.trains[0], section, reverse=True)
    assert all(p.limit_mps <= 5 for p in profile.points if 800 <= p.position_m <= 900)
    assert all(p.limit_mps > 5 for p in profile.points if 100 <= p.position_m <= 200)


def test_energy_matches_kinetic_work_when_resistance_and_aux_are_zero(small):
    train = small.trains[0].model_copy(
        update={"rolling_coefficient": 0, "drag_n_per_mps2": 0, "auxiliary_power_w": 0}
    )
    profile = build_speed_profile(train, small.sections[0])
    peak = max(p.speed_mps for p in profile.points)
    expected = 0.5 * train.mass_kg * peak**2 / train.traction_efficiency / 3_600_000
    assert profile.energy_kwh == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_target_rejected(small, duration):
    with pytest.raises(ValueError):
        build_speed_profile(small.trains[0], small.sections[0], duration)


def test_slower_cruise_reduces_energy_for_low_auxiliary_load(small):
    train = small.trains[0]
    fastest = build_speed_profile(train, small.sections[0])
    slow = build_speed_profile(train, small.sections[0], fastest.duration_s * 1.2)
    assert slow.energy_kwh < fastest.energy_kwh
