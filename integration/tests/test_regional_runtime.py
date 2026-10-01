from fastapi.testclient import TestClient

from backend.app import main
from backend.app.fleet_edit import Addition, FleetEdit, prepare_edit
from backend.app.integration import LogicSimulator
from integration.main import app


def test_network_api_day_demand_and_arbitrary_fleet_route(monkeypatch):
    sim = LogicSimulator("akmola_network", "reference_day")
    monkeypatch.setattr(main, "sim", sim)
    monkeypatch.setattr(main, "demo_mode", True)
    client = TestClient(app)
    state = client.get("/api/state").json()
    assert len(state["trains"]) == 28
    assert not sim.plan_violations()
    assert state["traffic"]["reference_month"] == "2026-05"
    catalog = client.get("/api/logic/regional-catalog").json()
    assert catalog["coverage"]["reachable_directed_pairs"] == 9702
    assert catalog["source"]["synthetic_edges_used"] is False
    operations = client.get("/api/logic/operations").json()
    assert operations["rotation"]["feasible"]
    assert operations["rotation"]["source"] == "simulation_assumption"
    request = {"supplies": {"OSM_4020137343": 20}, "demands": {"OSM_4035828316": 20}}
    assert client.post("/api/logic/empty-wagons", json=request).json()["feasible"]
    edited = prepare_edit(
        sim.state,
        FleetEdit(
            epoch=state["epoch"],
            add=[
                Addition(
                    kind="freight",
                    direction=1,
                    origin="OSM_4020137343",
                    destination="OSM_4035828316",
                )
            ],
        ),
    )
    # prepare_edit returns a replacement state, never mutates the live scenario.
    assert len(sim.state["fleet"]) == 28
    assert len(edited["fleet"]) == 29
    added = next(t for t in edited["fleet"] if t["id"].startswith("USR-"))
    assert added["route"][0] == "OSM_4020137343" and added["route"][-1] == "OSM_4035828316"


def test_network_snapshot_position_matches_each_leg_orientation():
    sim = LogicSimulator("akmola_network")
    topology = sim.state["topology"]
    positions = {s["id"]: s["position_m"] for s in topology["stations"]}
    for move in sim.state["active_plan"]["movements"][::9]:
        sim.state["sim_time_s"] = (move["start_s"] + move["end_s"]) / 2
        train = next(t for t in sim.snapshot()["trains"] if t["id"] == move["train_id"])
        a, b = positions[move["origin"]], positions[move["destination"]]
        assert min(a, b) <= train["position_m"] <= max(a, b)
        section = next(s for s in topology["sections"] if s["id"] == move["section_id"])
        assert train["direction"] == (1 if move["origin"] == section["from_station"] else -1)


def test_physics_budget_and_reset_preserve_network_context(monkeypatch):
    sim = LogicSimulator("akmola_network", "reference_day")
    monkeypatch.setattr(main, "sim", sim)
    monkeypatch.setattr(main, "demo_mode", True)
    monkeypatch.setattr(main, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "publish", lambda: None)
    monkeypatch.setattr(main, "queue_replan", lambda: None)
    client = TestClient(app)
    epoch = sim.state["epoch"]
    assert (
        client.post(
            "/api/dispatch/planning-budget", json={"epoch": epoch, "seconds": 10}
        ).status_code
        == 200
    )
    assert sim.state["planning_budget_s"] == 10
    assert (
        client.post(
            "/api/dispatch/planning-budget", json={"epoch": epoch, "seconds": 31}
        ).status_code
        == 422
    )
    move = sim.state["active_plan"]["movements"][0]
    body = {
        "epoch": epoch,
        "section_id": move["section_id"],
        "grade_permille": 8,
        "curve_radius_m": 400,
        "cant_mm": 80,
    }
    assert client.post("/api/dispatch/section-physics", json=body).status_code == 200
    section = next(s for s in sim.state["scenario"]["sections"] if s["id"] == move["section_id"])
    assert section["grade_permille"] == 8 and section["curve_radius_m"] == 400
    sim.state["sim_time_s"] = move["start_s"] + 1
    assert client.post("/api/dispatch/section-physics", json=body).status_code == 409
    response = client.post("/api/simulation/reset")
    assert response.status_code == 200, response.text
    assert sim._corridor == "akmola_network" and sim._traffic_profile == "reference_day"
    assert len(sim.state["fleet"]) == 28 and sim.state["sim_time_s"] == 0
