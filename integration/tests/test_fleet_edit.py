import copy

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.fleet_edit import Addition, FleetEdit, prepare_edit
from backend.app.integration import LogicSimulator, compact_state
from backend.app.schemas import Plan, Scenario
from backend.app.validation.plan import validate_plan
from integration.main import app


@pytest.fixture(scope="module")
def initial():
    return LogicSimulator()


def test_batch_keeps_existing_movements_and_metrics_and_adds_both_directions(initial):
    state = compact_state(initial.state)
    state["sim_time_s"] = 500
    before = copy.deepcopy(state)
    edit = FleetEdit(
        epoch=state["epoch"],
        add=[Addition(kind="passenger", direction=-1), Addition(kind="freight", direction=1)],
        remove=["SYN-P07", "SYN-F08"],
    )
    result = prepare_edit(state, edit)
    assert state == before
    assert len(result["fleet"]) == 8
    old = Plan.model_validate(state["active_plan"]["_native"])
    plan = Plan.model_validate(result["active_plan"]["_native"])
    assert not validate_plan(Scenario.model_validate(result["scenario"]), plan, old)
    retained = {m.train_id for m in plan.movements if m.train_id.startswith("SYN")}
    assert [m for m in old.movements if m.train_id in retained] == [
        m for m in plan.movements if m.train_id in retained
    ]
    added = [t for t in result["fleet"] if t["id"].startswith("USR")]
    assert {t["direction"] for t in added} == {-1, 1}
    assert all(t["release_s"] >= 620 for t in added)
    assert next(t for t in added if t["kind"] == "freight")["destination"] == "ASTANA_1"
    assert result["active_plan"]["forecast"]["applicable"]
    assert result["active_plan"]["forecast"]["quality_index"] is not None
    assert len(result["baseline"]["_native"]["movements"]) == len(plan.movements)


@pytest.mark.parametrize(
    "case", ["started", "unknown", "duplicate", "minimum", "epoch", "empty", "infeasible"]
)
def test_invalid_batch_leaves_original_unchanged(initial, case):
    state = compact_state(initial.state)
    before = copy.deepcopy(state)
    values = {"epoch": state["epoch"]}
    if case == "started":
        values["remove"] = ["SYN-P01"]
    if case == "unknown":
        values["remove"] = ["missing"]
    if case == "duplicate":
        values["remove"] = ["SYN-P07"] * 2
    if case == "minimum":
        values["remove"] = ["SYN-P04", "SYN-P05", "SYN-P06", "SYN-P07"]
    if case == "epoch":
        values.update(epoch="old", remove=["SYN-P07"])
    if case == "infeasible":
        state["scenario"]["horizon_s"] = (
            int(max(m["end_s"] for m in state["active_plan"]["movements"])) + 100
        )
        state["scenario"]["evaluation_end_s"] = min(
            state["scenario"]["evaluation_end_s"], state["scenario"]["horizon_s"]
        )
        values["add"] = [Addition(kind="freight", direction=1, ready_in_s=14400)]
        before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        prepare_edit(state, FleetEdit(**values))
    assert state == before


def test_permissions_step_and_atomic_fleet_route(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fleet.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("VIEWER_PASSWORD", "test-view")
    monkeypatch.setenv("DISPATCHER_PASSWORD", "test-dispatch")
    monkeypatch.setattr(main, "demo_mode", False)
    with TestClient(app) as client:
        epoch = main.sim.state["epoch"]
        body = {
            "epoch": epoch,
            "add": [{"kind": "passenger", "direction": 1}],
            "remove": ["SYN-P07"],
        }
        assert client.post("/api/fleet/batch", json=body).status_code == 401
        client.post("/api/auth/login", json={"role": "viewer", "password": "test-view"})
        assert client.post("/api/fleet/batch", json=body).status_code == 403
        assert client.post("/api/simulation/step", json={"seconds": 30}).status_code == 403
        client.post("/api/auth/login", json={"role": "dispatcher", "password": "test-dispatch"})
        state = client.post("/api/simulation/step", json={"seconds": 30}).json()
        assert state["sim_time_s"] == 30 and not state["running"]
        assert client.post("/api/simulation/step", json={"seconds": -1}).status_code == 422
        changed = client.post("/api/fleet/batch", json=body)
        assert changed.status_code == 200, changed.text
        assert len(changed.json()["trains"]) == 8
        assert changed.json()["plan"]["forecast"]["applicable"]
        main.sim.state["awaiting_plan"] = True
        before = copy.deepcopy(main.sim.state)
        assert client.post("/api/fleet/batch", json=body).status_code == 409
        assert client.post("/api/simulation/step", json={"seconds": 30}).status_code == 409
        assert main.sim.state == before


def test_cancelling_queued_train_keeps_consumed_energy(initial):
    sim = copy.deepcopy(initial)
    stops = sim.state["active_plan"]["_native"]["stops"]
    train = next(
        t
        for t in sim.state["fleet"]
        if next(s["arrival_s"] for s in stops if s["train_id"] == t["id"]) > t["release_s"] + 1
    )
    arrival = next(s["arrival_s"] for s in stops if s["train_id"] == train["id"])
    sim.state["sim_time_s"] = (train["release_s"] + arrival) / 2
    before = sim.snapshot()["metrics"]["energy_kwh"]
    candidate = prepare_edit(
        compact_state(sim.state), FleetEdit(epoch=sim.state["epoch"], remove=[train["id"]])
    )
    sim.state = candidate
    sim._validation_cache = None
    assert sim.snapshot()["metrics"]["energy_kwh"] == pytest.approx(before)
    assert candidate["scenario"]["metadata"]["cancelled_energy_kwh"] > 0
    sim.state["running"] = True
    sim.tick(100000)
    assert sim.snapshot()["metrics"]["energy_kwh"] == pytest.approx(
        candidate["active_plan"]["forecast"]["energy_kwh"]
    )


def test_edit_rejects_conditions_changed_during_calculation(tmp_path, monkeypatch):
    from backend.app import fleet_edit

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'race.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setattr(main, "demo_mode", True)
    original = fleet_edit.prepare_edit

    def changed(state, edit):
        result = original(state, edit)
        main.sim.state["constraint_version"] += 1
        return result

    monkeypatch.setattr(fleet_edit, "prepare_edit", changed)
    with TestClient(app) as client:
        before = main.sim.state["active_plan"]["id"]
        ids = [t["id"] for t in main.sim.state["fleet"]]
        response = client.post(
            "/api/fleet/batch",
            json={"epoch": main.sim.state["epoch"], "add": [{"kind": "passenger", "direction": 1}]},
        )
        assert response.status_code == 409
        assert main.sim.state["active_plan"]["id"] == before
        assert [t["id"] for t in main.sim.state["fleet"]] == ids
        assert not main.sim.replanning
