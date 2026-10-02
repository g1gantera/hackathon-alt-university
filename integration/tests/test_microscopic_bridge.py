import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import main, microscopic_api
from backend.app.integration import LogicSimulator
from backend.app.microscopic.bridge import from_integrated
from backend.app.microscopic.engine import Engine
from backend.app.microscopic.models import Stop, TrainSpec
from integration.main import app


@pytest.fixture
def client(monkeypatch):
    sim = SimpleNamespace(
        state={
            "epoch": "micro-test",
            "state_version": 0,
            "constraint_version": 0,
            "active_plan": {"id": "source-plan"},
            "running": False,
        },
        replanning=False,
    )
    monkeypatch.setattr(main, "sim", sim)
    monkeypatch.setattr(main, "demo_mode", True)
    monkeypatch.setattr(main, "publish", lambda: None)
    monkeypatch.setattr(microscopic_api, "_run", None)
    yield TestClient(app)
    if microscopic_api._run is not None:
        microscopic_api._run["engine"].history.db.close()


def test_api_demo_motion_isolated_revisions_and_recovery(client):
    state = client.post(
        "/api/execution/create", json={"epoch": "micro-test", "source": "passing"}
    ).json()
    assert len(state["trains"]) == 2 and not state["safety_errors"]
    original = copy.deepcopy(main.sim.state)
    body = {"run_id": state["run_id"], "revision": 0, "seconds": 10}
    response = client.post("/api/execution/step", json=body)
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved["sim_time"] == 10 and len(moved["frames"]) == 50
    assert not moved["safety_errors"] and main.sim.state == original
    assert client.post("/api/execution/step", json=body).status_code == 409
    tid = moved["trains"][0]["id"]
    incident = {
        "run_id": moved["run_id"],
        "revision": moved["revision"],
        "incident": {
            "id": "TEST-STOP",
            "kind": "train_breakdown",
            "asset_type": "train",
            "asset_id": tid,
            "duration_s": None,
        },
    }
    response = client.post("/api/execution/incidents", json=incident)
    assert response.status_code == 200, response.text
    held = response.json()
    assert (
        client.post(
            "/api/execution/clear",
            json={
                "run_id": held["run_id"],
                "revision": held["revision"],
                "incident_id": "TEST-STOP",
            },
        ).status_code
        == 200
    )
    main.sim.state["epoch"] = "reset"
    assert client.get("/api/execution/state").status_code == 409


def test_viewer_can_read_but_cannot_drive(client, monkeypatch):
    client.post(
        "/api/execution/create", json={"epoch": "micro-test", "source": "passing"}
    ).raise_for_status()
    monkeypatch.setattr(main, "demo_mode", False)
    # Existing session representation is verified through a real login instead.
    monkeypatch.setenv("VIEWER_PASSWORD", "test-micro-password")
    client.post(
        "/api/auth/login", json={"role": "viewer", "password": "test-micro-password"}
    ).raise_for_status()
    assert client.get("/api/execution/state").status_code == 200
    assert (
        client.post(
            "/api/execution/create", json={"epoch": "micro-test", "source": "passing"}
        ).status_code
        == 403
    )


def test_import_all_trains_and_timetable_no_source_mutation():
    sim = LogicSimulator("kokshetau_burabay")
    original = copy.deepcopy(sim.state)
    engine = from_integrated(sim.state)
    assert set(engine.trains) == {t["id"] for t in sim.state["fleet"]}
    stops = {
        (s["train_id"], s["station_id"]): s for s in sim.state["active_plan"]["_native"]["stops"]
    }
    for t in sim.state["scenario"]["trains"]:
        imported = engine.trains[t["id"]]
        assert imported.spec.departure_s == stops[t["id"], t["route"][0]]["departure_s"]
        assert len(imported.stop_points) == len(t["route"]) - 1
        assert all(a[0] in engine.network.allowed for a in imported.route)
    assert sim.state == original
    json.dumps(engine.snapshot(), allow_nan=False)
    engine.history.db.close()


def test_failed_full_corridor_import_is_atomic():
    sim = LogicSimulator()
    original = copy.deepcopy(sim.state)
    with pytest.raises(ValueError, match="Импорт отменён"):
        from_integrated(sim.state)
    assert sim.state == original


def test_dwell_never_departs_before_imported_departure():
    net = microscopic_api.network()
    engine = Engine(net)
    vertex = net.endpoint(engine.demo["bypass_route"][0])
    t = engine.add_train(
        TrainSpec(
            id="DWELL",
            name="DWELL",
            origin=engine.demo["origin"],
            destination=engine.demo["destination"],
            stops=[Stop(vertex=vertex, dwell_s=1, earliest_departure_s=5000)],
        )
    )
    # A consistent stopped approach, then regular physics through arrival.
    t.x = t.next_stop["distance"] - 2
    t.authority = t.x
    engine.replan("approach")
    engine.advance(30)
    assert t.state == "dwelling" and t.dwell_until >= 5000
    engine.advance(5)
    assert t.speed == 0 and t.state == "dwelling"
    engine.history.db.close()


def test_import_does_not_silently_drop_native_incidents():
    sim = LogicSimulator("kokshetau_burabay")
    sim.state["scenario"]["trains"][0]["section_hold_s"] = {"test": 30}
    with pytest.raises(ValueError, match="Импорт ограничений"):
        from_integrated(sim.state)
