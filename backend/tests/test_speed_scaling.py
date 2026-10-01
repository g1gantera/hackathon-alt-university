"""Long-section checks against the original scalar kinematics recurrence."""

import math

import numpy as np
import pytest

from app.advisory.speed import _envelope, build_speed_profile
from app.schemas import SpeedLimit


def scalar_envelope(x, limits, train, cap, initial_speed, final_speed):
    """Independent sequential reference, including the exact end conditions."""
    speed = np.minimum(limits, cap)
    speed[0] = min(speed[0], initial_speed)
    for i, distance in enumerate(np.diff(x), start=1):
        speed[i] = min(
            speed[i], math.sqrt(speed[i - 1] ** 2 + 2 * train.acceleration_mps2 * distance)
        )
    speed[-1] = min(speed[-1], final_speed)
    for i in range(len(x) - 2, -1, -1):
        speed[i] = min(
            speed[i], math.sqrt(speed[i + 1] ** 2 + 2 * train.braking_mps2 * (x[i + 1] - x[i]))
        )
    if abs(speed[0] - initial_speed) > 1e-7 or abs(speed[-1] - final_speed) > 1e-7:
        return None
    sums = speed[:-1] + speed[1:]
    if np.any(sums <= 0):
        return None
    return speed, np.concatenate(([0.0], np.cumsum(2 * np.diff(x) / sums)))


@pytest.mark.parametrize("length_m", [1000, 40_000, 350_000])
def test_vector_envelope_matches_scalar_on_random_limits(small, length_m):
    rng = np.random.default_rng(8321)
    for _ in range(16):
        train = small.trains[0].model_copy(
            update={
                "acceleration_mps2": float(rng.uniform(0.1, 0.8)),
                "braking_mps2": float(rng.uniform(0.2, 1.0)),
            }
        )
        x = np.r_[0, np.sort(rng.uniform(0, length_m, 1600)), length_m]
        limits = rng.uniform(1, 40, len(x))
        initial, final = rng.uniform(0, 8, 2)
        # Alternate ordinary stops and moving boundary conditions. Random
        # low limits also exercise cases with no reachable boundary speed.
        if rng.integers(2):
            initial = final = 0
        cap = float(rng.uniform(0.1, 40))
        actual = _envelope(x, limits, train, cap, initial, final)
        expected = scalar_envelope(x, limits, train, cap, initial, final)
        assert (actual is None) == (expected is None)
        if actual is not None:
            np.testing.assert_allclose(actual[0], expected[0], rtol=1e-8, atol=1e-8)
            np.testing.assert_allclose(actual[1], expected[1], rtol=1e-8, atol=1e-6)


@pytest.mark.parametrize("cap", [1e-5, 0.1, 8, 35])
def test_very_slow_caps_preserve_long_section_envelope(small, cap):
    x = np.linspace(0, 350_000, 14_001)
    limits = np.full_like(x, 35)
    actual = _envelope(x, limits, small.trains[0], cap, 0, 0)
    expected = scalar_envelope(x, limits, small.trains[0], cap, 0, 0)
    np.testing.assert_allclose(actual[0], expected[0], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(actual[1], expected[1], rtol=1e-9, atol=1e-6)


@pytest.mark.parametrize("initial,final", [(6.0, 9.0), (0.0, 0.0)])
def test_long_section_profile_meets_boundary_time_and_physics(small, initial, final):
    train = small.trains[0]
    section = small.sections[0].model_copy(
        update={
            "length_m": 40_000,
            "speed_limits": [
                SpeedLimit(start_m=7210, end_m=8355, speed_mps=8),
                SpeedLimit(start_m=24_050, end_m=25_000, speed_mps=12),
            ],
        }
    )
    boundary = {"initial_speed_mps": initial, "final_speed_mps": final}
    fastest = build_speed_profile(train, section, **boundary)
    target = math.ceil(fastest.duration_s * 1.2)
    profile = build_speed_profile(train, section, target, **boundary)
    assert profile.status == "FEASIBLE"
    assert profile.duration_s == pytest.approx(target, abs=1e-5)
    assert profile.points[0].speed_mps == pytest.approx(initial, abs=1e-7)
    assert profile.points[-1].speed_mps == pytest.approx(final, abs=1e-7)
    for left, right in zip(profile.points, profile.points[1:]):
        distance = right.position_m - left.position_m
        acceleration = (right.speed_mps**2 - left.speed_mps**2) / (2 * distance)
        assert -train.braking_mps2 - 1e-8 <= acceleration <= train.acceleration_mps2 + 1e-8
        assert right.speed_mps <= right.limit_mps + 1e-8
        assert right.time_s > left.time_s


def test_moving_boundary_cannot_be_lowered_by_a_cruise_cap(small):
    x = np.linspace(0, 40_000, 1601)
    limits = np.full_like(x, 20)
    assert _envelope(x, limits, small.trains[0], 5, 6, 0) is None
    assert _envelope(x, limits, small.trains[0], 5, 0, 6) is None


def test_returned_profiles_do_not_share_mutable_points(small):
    first = build_speed_profile(small.trains[0], small.sections[0])
    expected = first.points[1].speed_mps
    first.points[1].speed_mps = -99
    second = build_speed_profile(small.trains[0], small.sections[0])
    assert second.points[1].speed_mps == expected
