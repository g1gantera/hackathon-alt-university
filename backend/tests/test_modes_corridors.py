import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.control_mode import clearance_key
from backend.app.integration import LogicSimulator
from integration.main import app


def test_manual_clock_stops_before_departure_and_requires_each_route():
    sim = LogicSimulator()
    sim.state["running"] = True
    moves = sorted(sim.state["active_plan"]["movements"], key=lambda m: m["start_s"])
    first = moves[0]
    sim.tick(100000)
    assert sim.state["sim_time_s"] < first["start_s"]
    assert not any(t["status"] == "moving" for t in sim.snapshot()["trains"])
    sim.state["route_clearances"] = [clearance_key(sim.state["active_plan"]["id"], first)]
    sim.tick(1)
    assert (
        next(t for t in sim.snapshot()["trains"] if t["id"] == first["train_id"])["status"]
        == "moving"
    )
    sim.tick(100000)
    assert sim.state["sim_time_s"] < moves[1]["start_s"]
    sim.state["control_mode"] = "automatic"
    sim.tick(100000)
    assert sim.snapshot()["metrics"]["completed_trips"] > 0


@pytest.mark.parametrize("corridor,count", [("astana_atbasar", 11), ("astana_ereymentau", 6)])
def test_new_corridors_have_connected_geometry_and_valid_both_direction_fleet(corridor, count):
    sim = LogicSimulator(corridor)
    assert len(sim.state["topology"]["stations"]) == count
    assert len(sim.state["fleet"]) == 8
    assert {t["direction"] for t in sim.state["fleet"]} == {-1, 1}
    assert not sim.plan_violations()
    assert all(s["length_m"] > 0 for s in sim.state["topology"]["sections"])
    assert len({s["id"] for s in sim.state["topology"]["stations"]}) == count
    sim.reset()
    assert sim.state["topology"]["corridor_id"] == corridor


def test_manual_clearance_is_scoped_to_plan_and_modes_are_authorized(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'control.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("DISPATCHER_PASSWORD", "test-control")
    monkeypatch.setattr(main, "demo_mode", False)
    monkeypatch.setattr(main, "queue_replan", lambda: None)
    with TestClient(app) as client:
        state = main.sim.state
        move = state["active_plan"]["movements"][0]
        body = dict(
            epoch=state["epoch"],
            plan_id=state["active_plan"]["id"],
            train_id=move["train_id"],
            section_id=move["section_id"],
        )
        assert client.post("/api/control/authorize", json=body).status_code == 401
        client.post("/api/auth/login", json=dict(role="dispatcher", password="test-control"))
        assert (
            client.post("/api/control/authorize", json={**body, "plan_id": "stale"}).status_code
            == 409
        )
        assert client.post("/api/control/authorize", json=body).status_code == 200
        assert len(state["route_clearances"]) == 1
        assert client.post("/api/control/authorize", json=body).status_code == 200
        assert len(state["route_clearances"]) == 1
        assert (
            client.post(
                "/api/control/mode", json=dict(epoch=state["epoch"], mode="automatic")
            ).status_code
            == 200
        )
        assert state["awaiting_plan"] and state["control_mode"] == "automatic"
        assert client.post("/api/control/authorize", json=body).status_code == 409


def test_automatic_replanning_applies_only_valid_current_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auto.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setattr(main, "demo_mode", True)
    with TestClient(app) as client:
        old = main.sim.state["active_plan"]["id"]
        response = client.post(
            "/api/control/mode", json=dict(epoch=main.sim.state["epoch"], mode="automatic")
        )
        assert response.status_code == 200
        import time

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = client.get("/api/state").json()
            if not state["replanning"] and not state["awaiting_plan"]:
                break
            time.sleep(0.1)
        assert not state["awaiting_plan"]
        assert state["active_plan_id"] != old
        assert not main.sim.plan_violations()
