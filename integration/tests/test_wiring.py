"""Contract checks for the added wiring and preservation of component sources."""

import hashlib
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main as backend
from integration.main import MAP_ADAPTER, app

ROOT = Path(__file__).resolve().parents[2]


def test_component_sources_preserved_except_documented_approved_changes():
    manifest = json.loads((ROOT / "integration/source-manifest.json").read_text())
    assert set(manifest["sources"]) == {"M_part", "era", "logic"}
    changes = json.loads((ROOT / "integration/backend-changes.json").read_text())
    assert set(changes) == {"backend/app/integration.py", "backend/app/main.py"}
    frontend_changes = json.loads((ROOT / "integration/frontend-changes.json").read_text())
    assert set(frontend_changes) == {"frontend/src/types.ts", "frontend/src/App.tsx"}
    changes.update(frontend_changes)
    assert all(changes.values())
    for entry in manifest["files"]:
        if entry["path"] in changes:
            continue
        assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"], (
            entry
        )


def test_shared_api_websocket_and_original_map(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'wiring.sqlite'}")
    monkeypatch.setattr(backend, "demo_mode", True)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        source = (ROOT / "map.html").read_bytes()
        assert client.get("/map.html").content == source
        assert client.get("/network/").text.replace(MAP_ADAPTER, "") == source.decode()
        assert client.get("/network/map_data.js").content == (ROOT / "map_data.js").read_bytes()
        assert client.get("/network.json").content == (ROOT / "network.json").read_bytes()
        assert client.get("/api/integration").json()["simulation_count"] == 1
        state = client.get("/api/state").json()
        assert state["epoch"] == backend.sim.state["epoch"]
        assert state["engine"] == "logic" and len(state["trains"]) == 8
        with client.websocket_connect("/ws") as websocket:
            initial = websocket.receive_json()
            assert initial["payload"]["epoch"] == state["epoch"]
            assert initial["payload"]["active_plan_id"] == state["active_plan_id"]
        assert len(client.get("/api/logic/scenarios").json()["scenarios"]) == 19
        assert client.get("/api/trains/" + state["trains"][0]["id"] + "/profile").json()[
            "reachable"
        ]
        assert client.post("/api/simulation/start").json()["running"]
        assert not client.post("/api/simulation/pause").json()["running"]
        # No new unauthenticated route to a simulation is introduced by the wrapper.
        monkeypatch.setattr(backend, "demo_mode", False)
        monkeypatch.setenv("VIEWER_PASSWORD", "integration-test")
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/integration").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"role": "viewer", "password": "integration-test"}
            ).status_code
            == 200
        )
        assert client.get("/api/integration").status_code == 200
        assert client.get("/api/state").status_code == 200
        assert client.post("/api/simulation/start").status_code == 403


def test_built_dispatcher_and_assets_are_served():
    index = ROOT / "frontend/dist/index.html"
    # Production assets are checked after the existing frontend's build.
    assert index.is_file(), "Run pnpm --dir frontend build before integration tests"
    with TestClient(app) as client:
        assert client.get("/dispatcher/").content == index.read_bytes()
        for url in re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.read_text()):
            assert client.get(url).status_code == 200, url
