"""Native logic must remain authoritative through the live API and simulation."""

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.advisory.speed import build_speed_profile
from backend.app.integration import LogicSimulator, config_for, scenario_for
from backend.app.live_logic import prepare_profiles, resource_states, sample_profile
from backend.app.logic_api import diagnose
from backend.app.scenarios import INCIDENTS, incident_scenario
from backend.app.schemas import DavisResistance, Movement, Plan, Scenario, Section, Train
from integration.main import app


@pytest.fixture(scope="module")
def initial():
    return LogicSimulator()


@pytest.fixture
def sim(initial):
    return copy.deepcopy(initial)


def test_native_defaults_and_complete_trip_conservation(sim):
    expected = json.loads((main.ROOT / "config/metrics.json").read_text())
    assert config_for(sim.state).model_dump() == expected
    assert {t.priority for t in scenario_for(sim.state).trains} == {1, 3}
    assert sim.snapshot()["metrics"]["energy_kwh"] == 0
    forecast = sim.state["active_plan"]["forecast"]
    sim.state["running"] = True
    sim.tick(100_000)
    state = sim.snapshot()
    assert not state["running"] and state["metrics"]["completed_trips"] == 8
    assert state["metrics"]["energy_kwh"] == pytest.approx(forecast["energy_kwh"])
    assert state["metrics"]["weighted_delay_s"] == forecast["weighted_delay_s"]
    assert state["metrics"]["index"] == forecast["quality_index"]
    assert state["metrics"]["components"] == pytest.approx(forecast["components"])
    assert sum(sim.profile(t["id"])["energy_kwh"] for t in state["trains"]) == pytest.approx(
        forecast["energy_kwh"]
    )
    for train in state["trains"]:
        profile = sim.profile(train["id"])
        assert profile["points"][-1][-1] == pytest.approx(train["energy_kwh"])
        assert all(
            a[0] <= b[0] and a[4] <= b[4] + 1e-8
            for a, b in zip(profile["points"], profile["points"][1:])
        )
        assert train["distance_travelled_m"] == pytest.approx(
            abs(sim._position(train["destination"]) - sim._position(train["route"][0]))
        )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("davis", [None, DavisResistance(a=1.5, b=0.01, c=0.0002)])
def test_cell_motion_and_energy_include_stationary_recovery(reverse, davis):
    train = Train(
        id="T",
        kind="freight",
        route=["A", "B"],
        release_s=0,
        due_s=2000,
        mass_kg=800000,
        length_m=400,
        max_speed_mps=15,
        davis_resistance=davis,
        auxiliary_power_w=10000,
    )
    section = Section(
        id="AB", station_a="A", station_b="B", length_m=2000, max_speed_mps=20, grade_permille=5
    )
    native = build_speed_profile(train, section, reverse=reverse)
    hold = 120
    for point in native.points:
        point.time_s += hold
    native.points.insert(0, native.points[0].model_copy(update={"time_s": 0}))
    native.duration_s += hold
    native.auxiliary_energy_kwh += train.auxiliary_power_w * hold / 3_600_000
    native.energy_kwh += train.auxiliary_power_w * hold / 3_600_000
    movement = Movement(
        train_id="T",
        section_id="AB",
        origin="B" if reverse else "A",
        destination="A" if reverse else "B",
        start_s=0,
        end_s=2000,
        hold_s=hold,
    )
    projected = prepare_profiles(
        SimpleNamespace(trains=[train], sections=[section]),
        SimpleNamespace(movements=[movement]),
        {"T:AB": native},
    )["T:AB"]
    x, v, e = sample_profile(projected, 60)
    assert x == v == 0
    assert e == pytest.approx(10000 * 60 / 3_600_000)
    a, b = projected["points"][1:3]
    half_t = (a["time_s"] + b["time_s"]) / 2
    x, v, e = sample_profile(projected, half_t)
    assert x == pytest.approx(b["position_m"] / 4)  # acceleration from rest, not linear position
    assert v == pytest.approx(b["speed_mps"] / 2)
    assert e < projected["energy_kwh"]
    assert sample_profile(projected, 1e9)[2] == pytest.approx(native.energy_kwh)


def test_station_wait_and_reverse_origin(sim):
    first = min(sim.state["active_plan"]["movements"], key=lambda m: m["start_s"])
    sim.state["sim_time_s"] = first["start_s"] / 2
    train = next(t for t in sim.snapshot()["trains"] if t["id"] == first["train_id"])
    assert train["energy_kwh"] == pytest.approx(
        train["auxiliary_power_w"] * sim.state["sim_time_s"] / 3_600_000
    )
    assert train["distance_travelled_m"] == 0
    assert train["speed_mps"] == 0
    sim.state["sim_time_s"] = 0
    reverse = [t for t in sim.snapshot()["trains"] if t["direction"] == -1]
    assert reverse and all(t["distance_travelled_m"] == 0 for t in reverse)


@pytest.mark.parametrize("kind", INCIDENTS)
def test_every_incident_projects_native_resources(sim, kind):
    native = incident_scenario(
        Scenario.model_validate(sim.state["scenario"]),
        Plan.model_validate(sim.state["active_plan"]["_native"]),
        kind,
    )
    sim.state.update(
        scenario=native.model_dump(),
        constraint_version=1,
        sim_time_s=native.now_s,
        awaiting_plan=True,
    )
    for block in native.blocks:
        sim.state["sim_time_s"] = block.start_s
        sections, stations = resource_states(sim.state)
        if block.resource.startswith("main_track:"):
            _, sid, tid = block.resource.split(":")
            target = next(
                t for s in sections if s["id"] == sid for t in s["tracks"] if t["id"] == tid
            )
            assert target["status"] == ("closed" if block.kind == "closure" else "signal_failure")
        elif block.resource.startswith("section:"):
            target = next(s for s in sections if s["id"] == block.resource.removeprefix("section:"))
            assert all(
                t["status"] == ("closed" if block.kind == "closure" else "signal_failure")
                for t in target["tracks"]
            )
        elif block.resource.startswith("track:"):
            _, sid, tid = block.resource.split(":")
            target = next(
                t for s in stations if s["id"] == sid for t in s["tracks"] if t["id"] == tid
            )
            assert target["status"] == "closed"
    for section in native.sections:
        for limit in section.entry_speed_limits:
            sim.state["sim_time_s"] = limit.start_s
            target = next(s for s in resource_states(sim.state)[0] if s["id"] == section.id)
            assert target["entry_speed_factor"] <= limit.speed_factor
            assert limit.id in [limit_item["id"] for limit_item in target["speed_restrictions"]]
    sim.state["sim_time_s"] = native.now_s
    report = diagnose(sim.state)
    assert report["needs_replan"] and not report["forecast"]["applicable"]
    assert any(v["code"] != "STALE_PLAN" for v in report["violations"])


def test_speed_restriction_is_not_a_signal_failure_and_expiry_is_exclusive(sim):
    section = sim.state["scenario"]["sections"][0]
    entry = sim.add_incident("speed_restriction", section["id"], 600)
    sim.state["sim_time_s"] = entry["start_s"]
    target = resource_states(sim.state)[0][0]
    assert target["entry_speed_factor"] == 0.5 and target["status"] != "signal_failure"
    sim.state["sim_time_s"] = entry["end_s"]
    assert resource_states(sim.state)[0][0]["entry_speed_factor"] == 1


def test_settings_cannot_resurrect_invalid_plan(sim):
    sim.add_incident("closure", sim.state["scenario"]["sections"][0]["id"], 600)
    sim.update_settings(dict(sim.state["settings"], passenger_weight=20, freight_weight=0.1))
    assert {t.priority for t in scenario_for(sim.state).trains} == {1, 20}
    assert Scenario.model_validate(scenario_for(sim.state).model_dump())
    assert not sim.active_public_plan()["applicable"]
    assert not sim.active_public_plan()["forecast"]["applicable"]
    assert sim.active_public_plan()["forecast"]["track_load"] is None
    assert sim.state["awaiting_plan"]


def test_new_api_reports_use_shared_state_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'native.sqlite'}")
    monkeypatch.setattr(main, "demo_mode", True)
    with TestClient(app) as client:
        before = copy.deepcopy(main.sim.state)
        metrics = client.get("/api/logic/metrics").json()
        assert metrics["forecast"]["config"] == config_for(before).model_dump()
        assert metrics["forecast"]["applicable"]
        load = client.get("/api/logic/track-load").json()
        assert load["report"] == metrics["forecast"]["track_load"]
        rates = client.get("/api/logic/economics/rates-example").json()
        report = client.post(
            "/api/logic/economics",
            json={"reference_plan_id": before["active_plan"]["id"], "rates": rates},
        )
        assert report.status_code == 200, report.text
        assert report.json()["report"]["selected_plan_id"] == before["active_plan"]["id"]
        assert main.sim.state == before
        realism = client.post("/api/logic/realism", json={"runs": 1, "seed": 42})
        assert realism.status_code == 200, realism.text
        assert realism.json()["report"]["trains_per_run"] == 8
        assert realism.json()["report"]["raw_events_identical"]
        assert not realism.json()["applied_to_live"]
        assert main.sim.state == before
        assert client.post("/api/logic/realism", json={"runs": 11}).status_code == 422
        assert client.get("/api/logic/track-load?start_s=200&end_s=100").status_code == 409
        main.sim.add_incident("closure", main.sim.state["scenario"]["sections"][0]["id"], 600)
        assert not client.get("/api/logic/diagnostics").json()["forecast"]["applicable"]
        assert not client.get("/api/plans").json()["active"]["applicable"]
        assert client.get("/api/logic/track-load").status_code == 409
        assert (
            client.post(
                "/api/logic/economics",
                json={"reference_plan_id": before["active_plan"]["id"], "rates": rates},
            ).status_code
            == 409
        )
        monkeypatch.setattr(main, "demo_mode", False)
        assert client.get("/api/logic/metrics").status_code == 401
        monkeypatch.setenv("VIEWER_PASSWORD", "logic-viewer")
        client.post("/api/auth/login", json={"role": "viewer", "password": "logic-viewer"})
        assert client.get("/api/logic/metrics").status_code == 200
        assert client.post("/api/logic/realism", json={}).status_code == 403


def test_replanning_retries_when_an_incident_changes_the_worker_snapshot(sim, monkeypatch):
    import asyncio

    events, queued = [], []
    monkeypatch.setattr(main, "sim", sim)
    monkeypatch.setattr(main, "emit", lambda kind, payload: events.append((kind, payload)))
    monkeypatch.setattr(main, "publish", lambda: None)
    monkeypatch.setattr(
        main, "queue_replan", lambda: queued.append(sim.state["constraint_version"])
    )
    initial_snapshot = copy.deepcopy(sim.state)

    async def exercise():
        async def worker_result(*args):
            # A second restriction arrives while the first calculation is running.
            sim.add_incident("closure", sim.state["scenario"]["sections"][0]["id"], 600)
            return {"plans": [copy.deepcopy(initial_snapshot["active_plan"])]}

        monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", worker_result)
        await main.calculate("old-conditions", initial_snapshot)
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert not sim.plans
    assert sim.state["awaiting_plan"]
    assert queued == [1]
    assert any(kind == "replan.failed" for kind, _ in events)
    assert not any(kind == "replan.completed" for kind, _ in events)


def test_future_closure_invalidates_forecast_without_inventing_actual_conflicts(sim):
    before = sim.snapshot()["metrics"]
    sim.add_incident("closure", sim.state["scenario"]["sections"][0]["id"], 600)
    after = sim.snapshot()["metrics"]
    assert not after["plan_applicable"]
    assert after["applicable"]
    assert after["conflicts"] == 0
    assert after["components"] == before["components"]
    assert after["index"] == before["index"]
