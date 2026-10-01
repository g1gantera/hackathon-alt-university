import copy

import pytest

from backend.app.dispatch_policy import Assignment, Condition, assign, condition
from backend.app.integration import LogicSimulator
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.schemas import Scenario
from backend.app.validation.plan import validate_plan


@pytest.mark.parametrize("method", ["baseline", "solver"])
def test_manual_station_assignment_is_mandatory_and_independently_checked(small, method):
    small.trains[0].manual_station_tracks = {"A": "2"}
    result = build_baseline(small) if method == "baseline" else solve_plan(small, time_budget_s=1)
    assert result.plan is not None
    stop = next(s for s in result.plan.stops if s.train_id == "P1" and s.station_id == "A")
    assert stop.track_id == "2"
    stop.track_id = "1"
    assert any(v.code == "MANUAL_TRACK" for v in validate_plan(small, result.plan))


def test_auto_prefers_less_worn_station_when_other_conditions_equal(small):
    small.trains = small.trains[:1]
    small.metadata["track_wear"] = {"track:A:1": {"wear_pct": 70}, "track:A:2": {"wear_pct": 10}}
    for result in [build_baseline(small), solve_plan(small, time_budget_s=1)]:
        assert result.plan is not None
        assert next(s for s in result.plan.stops if s.station_id == "A").track_id == "2"


@pytest.fixture(scope="module")
def initial():
    return LogicSimulator()


def test_wrong_direction_and_committed_station_rejected_without_mutation(initial):
    sim = copy.deepcopy(initial)
    before = copy.deepcopy(sim.state)
    train = sim.state["fleet"][0]
    sid = sim.state["topology"]["sections"][0]["id"]
    with pytest.raises(ValueError):
        assign(
            sim, Assignment(epoch=sim.state["epoch"], train_id=train["id"], main_tracks={sid: "2"})
        )
    old = next(
        s for s in sim.state["active_plan"]["_native"]["stops"] if s["train_id"] == train["id"]
    )
    alternative = "SIM-2" if old["track_id"] == "SIM-1" else "SIM-1"
    with pytest.raises(ValueError):
        assign(
            sim,
            Assignment(
                epoch=sim.state["epoch"],
                train_id=train["id"],
                station_tracks={old["station_id"]: alternative},
            ),
        )
    assert sim.state == before


def test_manual_future_assignment_and_return_to_auto(initial):
    sim = copy.deepcopy(initial)
    t = sim.state["fleet"][-1]
    new = assign(
        sim,
        Assignment(
            epoch=sim.state["epoch"], train_id=t["id"], station_tracks={t["route"][0]: "SIM-2"}
        ),
    )
    assert next(x for x in new.trains if x.id == t["id"]).manual_station_tracks
    sim.state["scenario"] = new.model_dump()
    auto = assign(sim, Assignment(epoch=sim.state["epoch"], train_id=t["id"]))
    assert not next(x for x in auto.trains if x.id == t["id"]).manual_station_tracks


def test_wear_limits_preserve_history_and_clearing_removes_future_limit(initial):
    sim = copy.deepcopy(initial)
    sid = sim.state["topology"]["sections"][0]["id"]
    resource = f"main_track:{sid}:1"
    new = condition(sim, Condition(epoch=sim.state["epoch"], resource=resource, wear_pct=70))
    section = next(s for s in new.sections if s.id == sid)
    assert section.entry_speed_limits[-1].speed_factor == 0.6
    assert section.entry_speed_limits[-1].start_s == 1
    sim.state["scenario"] = new.model_dump()
    sim.state["sim_time_s"] = 200
    cleared = condition(sim, Condition(epoch=sim.state["epoch"], resource=resource, wear_pct=None))
    previous = next(s for s in cleared.sections if s.id == sid).entry_speed_limits[-1]
    assert previous.start_s == 1 and previous.end_s == 201
    assert resource not in cleared.metadata["track_wear"]


def test_critical_wear_closes_only_target_after_current_train_releases(initial):
    sim = copy.deepcopy(initial)
    move = sim.state["active_plan"]["movements"][0]
    sim.state["sim_time_s"] = move["start_s"] + 1
    resource = f"main_track:{move['section_id']}:{move['main_track_id']}"
    updated = condition(sim, Condition(epoch=sim.state["epoch"], resource=resource, wear_pct=90))
    block = next(b for b in updated.blocks if b.id.startswith("wear:"))
    assert block.resource == resource and block.start_s == move["release_s"]
    assert updated.metadata["track_wear"][resource]["source"] == "dispatcher_simulation"
    assert Scenario.model_validate(updated.model_dump())


@pytest.mark.parametrize("method", ["baseline", "solver"])
def test_main_track_assignment_and_validator(small, method):
    from backend.app.schemas import MainTrack

    small.sections[0].main_tracks = [MainTrack(id="1"), MainTrack(id="2")]
    small.trains[0].manual_main_tracks = {"AB": "2"}
    result = build_baseline(small) if method == "baseline" else solve_plan(small, time_budget_s=1)
    assert result.plan is not None
    move = next(m for m in result.plan.movements if m.train_id == "P1")
    assert move.main_track_id == "2"
    move.main_track_id = "1"
    assert any(v.code == "MANUAL_TRACK" for v in validate_plan(small, result.plan))


def test_policy_api_permissions_and_replanning_hold(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import main
    from integration.main import app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'policy.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("VIEWER_PASSWORD", "view-policy")
    monkeypatch.setenv("DISPATCHER_PASSWORD", "dispatch-policy")
    monkeypatch.setattr(main, "demo_mode", False)
    queued = []
    monkeypatch.setattr(main, "queue_replan", lambda: queued.append(True))
    with TestClient(app) as client:
        assert client.get("/api/dispatch/policy").status_code == 401
        client.post("/api/auth/login", json={"role": "viewer", "password": "view-policy"})
        info = client.get("/api/dispatch/policy").json()
        assert not info["wear"]  # no invented inspection data
        train = main.sim.state["fleet"][-1]
        body = {
            "epoch": info["epoch"],
            "train_id": train["id"],
            "station_tracks": {train["route"][0]: "SIM-2"},
        }
        assert client.post("/api/dispatch/assignment", json=body).status_code == 403
        client.post("/api/auth/login", json={"role": "dispatcher", "password": "dispatch-policy"})
        assert client.post("/api/dispatch/assignment", json=body).status_code == 200
        assert queued and main.sim.state["awaiting_plan"]
        resource = "main_track:" + main.sim.state["topology"]["sections"][0]["id"] + ":1"
        assert (
            client.post(
                "/api/dispatch/condition",
                json={"epoch": info["epoch"], "resource": resource, "wear_pct": 101},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/dispatch/condition",
                json={"epoch": info["epoch"], "resource": resource, "wear_pct": 60},
            ).status_code
            == 200
        )
        assert client.get("/api/dispatch/policy").json()["wear"][resource]["wear_pct"] == 60
