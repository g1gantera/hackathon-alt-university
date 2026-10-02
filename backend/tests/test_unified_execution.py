import copy
import json

import pytest

from backend.app.execution import UnifiedSimulator
from backend.app.integration import build_plans, default_settings, project_plan, validate_plan


def make_sim(scenario):
    sim = UnifiedSimulator.__new__(UnifiedSimulator)
    scenario.metadata["reserve_receiving_tracks"] = True
    from backend.app.planning.solver import solve_plan

    result = solve_plan(scenario, time_budget_s=2)
    assert result.plan, result.diagnostics
    sim.state = dict(
        engine="logic",
        scenario=scenario.model_dump(),
        settings=default_settings(),
        control_mode="automatic",
        route_clearances=[],
        sim_time_s=0.0,
        state_version=0,
        epoch="test",
        running=True,
        speed=30,
        incidents=[],
        constraint_version=0,
        awaiting_plan=False,
        topology={
            "stations": [
                {"id": s.id, "position_m": i * 1000, "tracks": len(s.tracks)}
                for i, s in enumerate(scenario.stations)
            ],
            "sections": [
                dict(id=s.id, from_station=s.station_a, to_station=s.station_b, length_m=s.length_m)
                for s in scenario.sections
            ],
        },
        fleet=[
            dict(t.model_dump(), type=t.kind, number=t.id, direction=1, destination=t.route[-1])
            for t in scenario.trains
        ],
    )
    plan = project_plan(sim.state, scenario, result.plan)
    sim.state.update(
        active_plan=plan, baseline=copy.deepcopy(plan), baseline_scenario=scenario.model_dump()
    )
    sim.state["execution"] = dict(trains={}, events=[], conflicts=[], revision=0, throats={})
    sim.plans = {}
    sim.replanning = False
    sim._validation_cache = None
    sim._cache_key = None
    sim._refresh()
    sim._ensure_trains()
    return sim


def run(sim, seconds=1000):
    for _ in range(seconds):
        sim.tick(1)
        snapshot = sim.snapshot()
        assert not sim.state["execution"]["conflicts"]
        json.dumps(snapshot, allow_nan=False)
        if sim.state["awaiting_plan"] and sim.ready_to_plan():
            result = build_plans(sim.state)
            assert result["plans"], result
            plan = result["plans"][0]
            assert not validate_plan(sim.state, plan)
            before = copy.deepcopy(sim.live)
            sim.install_plan(plan)
            for tid, r in sim.live.items():
                assert (r["x"], r["distance"], r["energy"]) == (
                    before[tid]["x"],
                    before[tid]["distance"],
                    before[tid]["energy"],
                )
        if not sim.state["running"]:
            break
    return sim


def test_opposing_trains_finish_without_sharing_blocks(small):
    sim = run(make_sim(small))
    assert all(r["complete"] for r in sim.live.values())
    assert all(r["distance"] == pytest.approx(1000, abs=0.1) for r in sim.live.values())
    assert len(sim.snapshot()["trains"]) == 2


def test_manual_clock_does_not_issue_a_departure(small):
    sim = make_sim(small)
    sim.state["control_mode"] = "manual"
    sim.tick(200)
    assert sim.state["sim_time_s"] == pytest.approx(200)
    assert all(r["distance"] == 0 for r in sim.live.values())
    from backend.app.control_mode import clearance_key

    move = sim.moves["P1"][0]
    sim.state["route_clearances"].append(clearance_key(sim.state["active_plan"]["id"], move))
    sim.tick(5)
    assert sim.live["P1"]["distance"] > 0
    assert sim.live["F1"]["distance"] == 0


def test_closure_brakes_instead_of_teleporting_and_recovers(small):
    small.trains = small.trains[:1]
    sim = make_sim(small)
    while sim.live["P1"]["v"] < 8:
        sim.tick(0.2)
    r = sim.live["P1"]
    old_x, old_v = r["x"], r["v"]
    now = sim.state["sim_time_s"]
    sim.state["scenario"]["blocks"].append(
        dict(
            id="failure",
            resource="section:AB",
            kind="closure",
            start_s=int(now),
            end_s=int(now) + 40,
        )
    )
    sim.state["constraint_version"] += 1
    sim.tick(0.2)
    assert r["x"] > old_x
    assert old_v - 0.5 * 0.2 - 1e-6 <= r["v"] < old_v
    run(sim, 500)
    assert r["complete"]


def test_replanning_pins_an_admitted_station_track(small):
    sim = make_sim(small)
    sim.tick(1)
    sim.state["awaiting_plan"] = True
    result = build_plans(sim.state)
    assert result["plans"]
    plan = result["plans"][0]
    assert not validate_plan(sim.state, plan)
    for tid, r in sim.live.items():
        if r["admitted"]:
            stop = next(
                s
                for s in plan["stops"]
                if s["train_id"] == tid and s["station_id"] == sim.trains[tid].route[0]
            )
            assert stop["track_id"] == r["track"]
    sim.state["execution"]["revision"] += 1
    assert validate_plan(sim.state, plan)[0]["code"] == "EXECUTION_CHANGED"


def test_resource_turn_waits_for_actual_completion(small):
    sim = make_sim(small)
    sim.state["scenario"]["metadata"]["resource_roster"] = {
        "units": [
            dict(
                id="L1", train_ids=["P1", "F1"], available_s=0, unavailable_s=4000, turnaround_s=50
            )
        ]
    }
    assert not sim.roster_ready("F1")[0]
    sim.live["P1"].update(complete=True, release=200)
    sim.state["sim_time_s"] = 249
    assert not sim.roster_ready("F1")[0]
    sim.state["sim_time_s"] = 250
    assert sim.roster_ready("F1")[0]


def test_same_direction_uses_separate_blocks_on_one_track(small):
    small.sections[0].block_length_m = 250
    first = small.trains[0]
    first.manual_station_tracks = {"A": "1", "B": "1"}
    first.min_dwell_s = 1
    second = first.model_copy(
        deep=True,
        update={"id": "P2", "release_s": 1, "manual_station_tracks": {"A": "2", "B": "2"}},
    )
    small.trains = [first, second]
    sim = make_sim(small)
    shared = False
    for _ in range(600):
        sim.tick(1)
        sim.snapshot()
        assert not sim.state["execution"]["conflicts"]
        shared |= sum(r["moving"] for r in sim.live.values()) == 2
        if sim.state["awaiting_plan"] and sim.ready_to_plan():
            p = build_plans(sim.state)["plans"][0]
            sim.install_plan(p)
        if not sim.state["running"]:
            break
    assert shared
    assert all(r["complete"] for r in sim.live.values())


def test_worker_snapshot_does_not_require_private_speed_arrays(small):
    from backend.app.integration import compact_state

    sim = make_sim(small)
    sim.tick(1)
    sim.state["awaiting_plan"] = True
    result = build_plans(compact_state(sim.state))
    assert result["plans"]
    sim.install_plan(result["plans"][0])
    run(sim)
    assert all(r["complete"] for r in sim.live.values())


def test_category_change_reaches_motion_and_future_optimizer(small):
    from backend.app.execution_planning import planning_scenario

    sim = make_sim(small)
    sim.state["scenario"]["trains"][1]["dispatch_category"] = "emergency"
    sim.state["constraint_version"] += 1
    sim._refresh()
    assert sim.trains["F1"].priority == 100
    assert next(t for t in planning_scenario(sim.state).trains if t.id == "F1").priority == 100
    assert next(t for t in sim.snapshot()["trains"] if t["id"] == "F1")["priority"] == 100


def test_manual_assignment_uses_actual_entry_not_elapsed_timetable(small):
    from backend.app.dispatch_policy import Assignment, assign
    from backend.app.schemas import MainTrack

    small.sections[0].main_tracks.append(MainTrack(id="2"))
    sim = make_sim(small)
    sim.state["control_mode"] = "manual"
    sim.tick(300)
    old = sim.moves["P1"][0]["main_track_id"]
    other = "2" if old == "1" else "1"
    body = Assignment(epoch=sim.state["epoch"], train_id="P1", main_tracks={"AB": other})
    assert assign(sim, body).trains[0].manual_main_tracks["AB"] == other
    sim.state["control_mode"] = "automatic"
    sim.tick(1)
    assert sim.live["P1"]["moving"]
    with pytest.raises(ValueError, match="уже вошёл"):
        assign(sim, body)
