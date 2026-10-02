import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import main
from backend.app.integration import LogicSimulator, compact_state
from backend.ingestion import service
from integration.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.sqlite'}")
    monkeypatch.setenv("METRIC_CONFIG_PATH", str(tmp_path / "metrics.json"))
    monkeypatch.delenv("INGEST_URL", raising=False)
    monkeypatch.setattr(main, "demo_mode", False)
    for role in ("VIEWER", "DISPATCHER", "ADMIN"):
        monkeypatch.setenv(role + "_PASSWORD", "test-" + role)
    with TestClient(app) as client:
        yield client


def login(client, role):
    assert (
        client.post(
            "/api/auth/login", json={"role": role, "password": "test-" + role.upper()}
        ).status_code
        == 200
    )


def test_config_roles_persistence_and_switches(client):
    assert client.get("/api/state").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    login(client, "viewer")
    state = client.get("/api/state").json()
    assert len(state["switches"]) == 13
    assert state["metrics"]["category"] == "Норма"
    config = client.get("/api/logic/metric-config").json()
    config.update(formula="weighted_geometric", normal_threshold=95, attention_threshold=60)
    assert client.put("/api/logic/metric-config", json=config).status_code == 403
    assert client.post("/api/replan").status_code == 403
    login(client, "dispatcher")
    assert client.put("/api/logic/metric-config", json=config).status_code == 403
    login(client, "admin")
    config["weights"]["energy"] = 0.9
    assert client.put("/api/logic/metric-config", json=config).status_code == 422
    config["weights"]["energy"] = 0.2
    assert client.put("/api/logic/metric-config", json=config).status_code == 200
    assert client.get("/api/state").json()["metrics"]["config"]["formula"] == "weighted_geometric"
    assert client.post("/api/simulation/reset").status_code == 200
    assert client.get("/api/logic/metric-config").json() == config
    assert client.get("/api/state").json()["metrics"]["index"] == 100


def test_batch_atomicity_and_one_calculation(client, monkeypatch):
    login(client, "dispatcher")
    before = client.get("/api/state").json()
    target = client.get("/api/topology").json()["sections"][0]["id"]
    items = [
        {"kind": "closure", "target_id": target, "duration_s": 600},
        {"kind": "closure", "target_id": "missing", "duration_s": 600},
    ]
    assert client.post("/api/incidents/batch", json={"incidents": items}).status_code == 404
    assert client.get("/api/state").json()["incidents"] == before["incidents"]
    queued = []
    monkeypatch.setattr(main, "queue_replan", lambda: queued.append(True) or {"job_id": "test"})
    sections = client.get("/api/topology").json()["sections"]
    items = [{"kind": "signal", "target_id": s["id"], "duration_s": 600} for s in sections[:10]]
    assert client.post("/api/incidents/batch", json={"incidents": items}).status_code == 200
    assert len(client.get("/api/state").json()["incidents"]) == 10
    assert queued == [True]


def test_normalization_auth_deduplication_and_gap(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "unit-token")
    snapshot = LogicSimulator().snapshot()
    snapshot["trains"][0].pop("speed_mps")
    snapshot["trains"][0]["speed_kmh"] = 36
    with TestClient(service.app) as client:
        packet = {"event_id": "normalization-test", "payload": snapshot}
        assert client.post("/events", json=packet).status_code == 401
        headers = {"Authorization": "Bearer unit-token"}
        response = client.post("/events", json=packet, headers=headers)
        assert response.status_code == 200
        normalized = response.json()
        assert normalized["payload"]["trains"][0]["speed_mps"] == 10
        assert "speed_kmh" not in normalized["payload"]["trains"][0]
        assert (
            client.post("/events", json=packet, headers=headers).json()["offset"]
            == normalized["offset"]
        )
        packet["payload"]["trains"][0]["speed_kmh"] = -1
        assert client.post("/events", json=packet, headers=headers).status_code == 422
    bus = service.EventBus(capacity=2)
    for i in range(3):
        bus.publish(str(i), {"value": i})
    assert bus.records[0]["offset"] == 2
    with pytest.raises(ValueError):
        bus.publish("2", {"value": 5})


def test_no_profile_arrays_in_planner_snapshot():
    sim = LogicSimulator()
    copied = compact_state(sim.state)
    assert "_profiles" not in copied["active_plan"]
    assert "_native" in copied["active_plan"]
    copied["scenario"]["trains"][0]["priority"] = 100
    assert sim.state["scenario"]["trains"][0]["priority"] != 100
