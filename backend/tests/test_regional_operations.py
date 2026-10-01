import copy

import pytest

from backend.app.advisory.speed import build_speed_profile
from backend.app.block_sections import signal_states
from backend.app.live_logic import prepare_profiles, sample_profile
from backend.app.metrics.quality import profile_plan
from backend.app.operations import empty_wagon_allocation, stock_rotation, wear_forecast
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.regional import regional_scenario, route_between
from backend.app.schemas import Block, Scenario, Section, Station, Track, Train
from backend.app.validation.plan import validate_plan


def small(blocks=1500):
    return Scenario(
        id="blocks",
        horizon_s=15000,
        stations=[
            Station(
                id=s, name=s, tracks=[Track(id="1", length_m=1500), Track(id="2", length_m=1500)]
            )
            for s in ("a", "b", "c")
        ],
        sections=[
            Section(
                id="ab",
                station_a="a",
                station_b="b",
                length_m=12000,
                max_speed_mps=25,
                block_length_m=blocks,
            ),
            Section(id="bc", station_a="b", station_b="c", length_m=3000, max_speed_mps=25),
        ],
        trains=[
            Train(
                id=str(i),
                kind="passenger",
                route=["a", "b"],
                release_s=i * 10,
                due_s=2000,
                length_m=200,
                mass_kg=400000,
                max_speed_mps=25,
            )
            for i in range(2)
        ],
    )


def test_blocks_allow_following_but_reject_collision_and_opposing_moves():
    s = small()
    result = solve_plan(s, time_budget_s=3)
    assert result.plan is not None, result.diagnostics
    p = result.plan
    assert not validate_plan(s, p)
    a, b = sorted(p.movements, key=lambda m: m.start_s)
    assert b.start_s < a.end_s  # multiple trains on one physical line
    broken = p.model_copy(deep=True)
    m = broken.movements[1]
    shift = m.start_s - broken.movements[0].start_s
    m.start_s -= shift
    m.end_s -= shift
    for stop in broken.stops:
        if stop.train_id == m.train_id:
            stop.arrival_s -= shift
            stop.departure_s -= shift
    assert any(
        v.code == "RESOURCE_CONFLICT" and v.resource.startswith("block:")
        for v in validate_plan(s, broken)
    )
    s.trains[1].route.reverse()
    r = solve_plan(s, time_budget_s=3)
    assert r.plan and not validate_plan(s, r.plan)
    a, b = sorted(r.plan.movements, key=lambda m: m.start_s)
    assert b.start_s >= a.end_s
    s.blocks = [Block(id="closed", resource="main_track:ab:1", start_s=0, end_s=1000)]
    p = solve_plan(s, time_budget_s=3).plan
    assert p and min(m.start_s for m in p.movements) >= 1000


def test_block_signal_and_regeneration_agree_with_live_energy():
    s = small()
    s.trains[0].regenerative_efficiency = 0.7
    s.trains[0].grid_receptivity = 0.8
    s.trains[0].regenerative_power_w = 1e6
    p = build_baseline(s, time_budget_s=3).plan
    assert p
    profiles = profile_plan(s, p)
    live = prepare_profiles(s, p, profiles)
    first = next(m for m in p.movements if m.train_id == "0")
    key = "0:ab"
    assert profiles[key].regenerated_energy_kwh > 0
    assert sample_profile(live[key], first.end_s - first.start_s)[2] == pytest.approx(
        profiles[key].energy_kwh
    )
    signals = signal_states(s, p, first.start_s + 150)
    assert signals and any(x["aspect"] == "red" for x in signals)
    assert all(x["aspect"] == "red" for x in signal_states(s, p, first.start_s + 150, False))
    ordinary = build_speed_profile(s.trains[1], s.sections[0])
    weak = s.trains[1].model_copy(update={"traction_power_w": 300000})
    limited = build_speed_profile(weak, s.sections[0])
    assert limited.minimum_duration_s > ordinary.minimum_duration_s
    curved = s.sections[0].model_copy(update={"curve_radius_m": 200})
    assert build_speed_profile(s.trains[1], curved).minimum_duration_s > ordinary.minimum_duration_s


def test_regional_shared_namespace_and_arbitrary_station_routes():
    s = regional_scenario()
    assert len(s.stations) > 60 and len(s.trains) >= 5
    a, b = "OSM_4020137343", "OSM_4035828316"
    path = route_between(s, a, b)
    assert path[0] == a and path[-1] == b
    assert len(set(x.id for x in s.stations)) == len(s.stations)
    with pytest.raises(ValueError):
        route_between(s, a, "unknown")
    p = build_baseline(s, time_budget_s=5).plan
    assert p and not validate_plan(s, p)
    occupied = {m.origin for m in p.movements}
    assert a in occupied


def test_stock_flow_and_wear_are_explicit_estimates():
    s = small()
    p = build_baseline(s, time_budget_s=3).plan
    original = copy.deepcopy(p)
    rotation = stock_rotation(s, p)
    assert rotation["feasible"] and rotation["fleet_required"] == 2
    flow = empty_wagon_allocation(s, {"a": 10}, {"c": 10})
    assert flow["feasible"] and flow["empty_wagon_km"] == 150
    with pytest.raises(ValueError):
        empty_wagon_allocation(s, {"bad": 10}, {"c": 10})
    with pytest.raises(ValueError):
        empty_wagon_allocation(s, {"a": 9}, {"c": 10})
    before = wear_forecast(s, p, 0)
    after = wear_forecast(s, p, 15000)
    assert not before["tracks"] and after["tracks"][0]["estimated_pct"] > 0
    assert original == p


def test_constructive_repair_keeps_history_and_rejects_occupied_damage():
    from backend.app.planning.repair import repair_plan

    s = small(0)
    p = build_baseline(s, time_budget_s=3).plan
    original = p.model_copy(deep=True)
    first = min(p.movements, key=lambda m: m.start_s)
    s.now_s = first.start_s + 10
    s.state_version += 1
    # Announced entry closure: the first train already entered and must finish.
    s.blocks = [
        Block(id="signal", resource="main_track:ab:1", start_s=s.now_s, end_s=2000, kind="signal")
    ]
    repaired = repair_plan(s, p, 2)
    assert repaired and not validate_plan(s, repaired, p)
    assert next(m for m in repaired.movements if m.train_id == first.train_id) == first
    assert original == p
    # A physical closure under the moving train must NOT be silently bypassed.
    s.blocks[0].kind = "closure"
    assert repair_plan(s, p, 2) is None


def test_invalid_old_block_profile_returns_violations_not_an_exception():
    s = small()
    p = build_baseline(s, time_budget_s=3).plan
    s.trains[0].section_hold_s = {"ab": 3600}
    violations = validate_plan(s, p)
    assert any(v.code == "SECTION_HOLD" for v in violations)
    assert any(v.code == "BLOCK_TIMING" for v in violations)
