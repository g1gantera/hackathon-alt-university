"""Live map assignments must agree with validated, exclusive reservations."""

import copy
import math

import pytest

from backend.app.integration import LogicSimulator, default_settings, project_plan
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Scenario, Section, Station, Track, Train
from backend.app.track_display import dispatch_state
from backend.app.validation.plan import validate_plan


@pytest.fixture(scope="module")
def initial():
    return LogicSimulator()


def test_queued_and_finished_trains_do_not_occupy_platforms_on_the_map(initial):
    sim = copy.deepcopy(initial)
    state = sim.snapshot()
    assert len(state["trains"]) == 8
    assert any(t["status"] == "scheduled" and not t["on_network"] for t in state["trains"])
    plan = sim.state["active_plan"]
    # Check exact admission/departure boundaries, including terminal release.
    for train in sim.state["fleet"]:
        stops = [s for s in plan["_native"]["stops"] if s["train_id"] == train["id"]]
        first, last = stops[0], stops[-1]
        for now in {
            max(0, first["arrival_s"] - 0.1),
            first["arrival_s"],
            first["departure_s"],
            last["arrival_s"],
            last["departure_s"],
        }:
            sim.state["sim_time_s"] = now
            item = next(t for t in sim.snapshot()["trains"] if t["id"] == train["id"])
            if now < first["arrival_s"]:
                assert not item["on_network"] and item["station_track_id"] is None
            elif now == first["arrival_s"]:
                assert item["on_network"] and item["station_track_id"] == first["track_id"]
                assert item["main_track_id"] is None
            elif now == first["departure_s"]:
                assert item["status"] == "moving" and item["main_track_id"]
                assert item["station_track_id"] is None
            elif now == last["arrival_s"]:
                assert item["status"] == "completed" and item["on_network"]
                assert item["station_track_id"] == last["track_id"]
            else:
                assert item["status"] == "completed" and not item["on_network"]


def test_display_lanes_are_distinct_without_inventing_extra_capacity(initial):
    topology = initial.state["topology"]
    native = initial.state["scenario"]
    for station, model in zip(topology["stations"], native["stations"]):
        assert {t["id"] for t in station["track_layout"]} == {t["id"] for t in model["tracks"]}
        a, b = [t["coordinate"] for t in station["track_layout"]]
        separation = math.hypot(
            (a[0] - b[0]) * 111195 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 111195
        )
        assert separation > 2, (station["id"], separation)
    for section in topology["sections"]:
        a, b = [t["geometry"] for t in section["main_tracks"]]
        assert len(a) == len(b) == len(section["geometry"])
        assert all(pa != pb for pa, pb in zip(a, b))
        assert all(t["geometry_source"].startswith("schematic") for t in section["main_tracks"])


def yielding_scenario():
    return Scenario(
        id="yield",
        horizon_s=4000,
        evaluation_end_s=2000,
        stations=[
            Station(id=x, name=x, tracks=[Track(id="1", length_m=800), Track(id="2", length_m=800)])
            for x in "AB"
        ],
        sections=[Section(id="AB", station_a="A", station_b="B", length_m=1000, max_speed_mps=20)],
        trains=[
            Train(
                id="F",
                kind="freight",
                priority=1,
                route=["A", "B"],
                release_s=0,
                due_s=1000,
                length_m=500,
                mass_kg=1000000,
                max_speed_mps=10,
                min_dwell_s=20,
            ),
            Train(
                id="P",
                kind="passenger",
                priority=3,
                route=["A", "B"],
                release_s=10,
                due_s=100,
                length_m=100,
                mass_kg=300000,
                max_speed_mps=20,
                min_dwell_s=20,
            ),
        ],
    )


def test_freight_yields_to_late_passenger_on_another_station_track():
    scenario = yielding_scenario()
    old = build_baseline(scenario).plan
    scenario.now_s = 5  # freight admitted, but has not departed
    plan = solve_plan(scenario, previous=old, time_budget_s=1).plan
    assert plan is not None and not validate_plan(scenario, plan, old)
    f, p = plan.movements
    assert p.start_s < f.start_s
    assert p.end_s < next(m.end_s for m in old.movements if m.train_id == "P")
    origins = [s for s in plan.stops if s.station_id == "A"]
    assert origins[0].track_id != origins[1].track_id
    assert max(s.arrival_s for s in origins) < min(s.departure_s for s in origins)
    public = project_plan({"settings": default_settings()}, scenario, plan)
    moves = [m for m in public["movements"] if m["train_id"] == "F"]
    stops = [s for s in public["stops"] if s["train_id"] == "F"]
    live = dispatch_state(scenario.trains[0].model_dump(), stops, moves, public["movements"], 25)
    assert live["on_network"] and live["station_track_id"] == origins[0].track_id
    assert live["waiting_for"] == ["P"] and "Пропускает P" in live["wait_reason"]
    assert live["next_departure_s"] == f.start_s
    assert f.start_s >= public["movements"][1]["release_s"]


def test_priority_does_not_move_a_train_that_already_entered_the_section():
    scenario = yielding_scenario()
    old = build_baseline(scenario).plan
    scenario.now_s = 40
    plan = solve_plan(scenario, previous=old, time_budget_s=1).plan
    assert plan is not None and not validate_plan(scenario, plan, old)
    assert plan.movements[0] == old.movements[0]
    assert plan.movements[1].start_s > plan.movements[0].end_s
