import copy

from backend.app.traffic_control import control_state, journal_events, yield_intervals


def example():
    trains = [
        dict(id="F", kind="freight", release_s=0, min_dwell_s=10, due_s=200),
        dict(id="P", kind="passenger", release_s=0, min_dwell_s=10, due_s=40),
    ]
    moves = [
        dict(
            train_id="P",
            section_id="AB",
            main_track_id="1",
            origin="A",
            destination="B",
            start_s=20,
            end_s=80,
            release_s=100,
        ),
        dict(
            train_id="F",
            section_id="AB",
            main_track_id="1",
            origin="A",
            destination="B",
            start_s=110,
            end_s=180,
            release_s=200,
        ),
    ]
    stops = [
        dict(
            train_id=t["id"],
            station_id="A",
            track_id=t["id"],
            arrival_s=0,
            departure_s=m["start_s"],
        )
        for t, m in zip(trains, moves[::-1])
    ]
    return dict(
        sim_time_s=20,
        scenario=dict(
            trains=trains,
            blocks=[],
            sections=[
                dict(
                    id="AB",
                    station_a="A",
                    station_b="B",
                    main_tracks=[dict(id="1", direction="a_to_b")],
                )
            ],
        ),
        active_plan=dict(id="test", movements=moves, _native=dict(stops=stops)),
    )


def test_signal_only_authorizes_reserved_train_and_fails_closed():
    state = example()
    signals, _ = control_state(state, [], [], True)
    assert signals[0]["aspect"] == "green"
    assert signals[0]["train_id"] == "P"
    for applicable in (False, True):
        state["scenario"]["blocks"] = [dict(resource="main_track:AB:1", start_s=0, end_s=200)]
        assert control_state(state, [], [], applicable)[0][0]["aspect"] == "red"
    state["scenario"]["blocks"] = []
    state["sim_time_s"] = 50
    assert control_state(state, [], [], True)[0][0]["aspect"] == "red"


def test_locked_switch_for_other_train_forbids_signal():
    state = example()
    locks = [dict(station_id="A", blocked_by=[], train_id="F")]
    assert control_state(state, [], locks, True)[0][0]["aspect"] == "red"


def test_yield_names_real_predecessor_and_logs_both_boundaries_when_step_skips():
    state = example()
    intervals = yield_intervals(state)
    assert len(intervals) == 1
    event = intervals[0]
    assert (event["train_id"], event["to_train_id"], event["start_s"], event["end_s"]) == (
        "F",
        "P",
        10,
        100,
    )
    assert "категория" in event["reason"] and "опоздание" in event["reason"]
    fleet = [dict(id="F")]
    control_state(state, fleet, [], True)
    assert fleet[0]["waiting_for"] == ["P"]
    state["sim_time_s"] = 110
    assert [e["type"] for e in journal_events(state, intervals)] == [
        "yield.started",
        "yield.finished",
    ]
    changed = copy.deepcopy(state)
    changed["active_plan"]["id"] = "replanned"
    assert journal_events(state, intervals) == journal_events(changed, intervals)


def test_different_track_is_not_a_reason_for_yield():
    state = example()
    state["active_plan"]["movements"][0]["main_track_id"] = "2"
    assert yield_intervals(state) == []


def test_categories_keep_emergency_above_passenger_in_every_strategy(small):
    from backend.app.traffic_control import CATEGORIES, dispatch_weight

    train = small.trains[0]
    for strategy in ("balanced", "passenger", "eco"):
        weights = []
        for name in CATEGORIES:
            train.dispatch_category = name
            weights.append(dispatch_weight(train, strategy))
        assert all(a > b for a, b in zip(weights, weights[1:]))


def test_category_api_authorization_and_committed_route_preservation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import main
    from backend.app.integration import scenario_for
    from integration.main import app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'category.sqlite'}")
    monkeypatch.setenv("DISPATCH_ENGINE", "logic")
    monkeypatch.setenv("VIEWER_PASSWORD", "test-view")
    monkeypatch.setenv("DISPATCHER_PASSWORD", "test-dispatch")
    monkeypatch.setattr(main, "demo_mode", False)
    monkeypatch.setattr(main, "queue_replan", lambda: None)
    with TestClient(app) as client:
        body = dict(
            epoch=main.sim.state["epoch"],
            train_id=main.sim.state["fleet"][-1]["id"],
            category="emergency",
        )
        assert client.post("/api/dispatch/category", json=body).status_code == 401
        client.post("/api/auth/login", json=dict(role="viewer", password="test-view"))
        assert client.post("/api/dispatch/category", json=body).status_code == 403
        client.post("/api/auth/login", json=dict(role="dispatcher", password="test-dispatch"))
        before = copy.deepcopy(main.sim.state["active_plan"]["movements"])
        assert (
            client.post("/api/dispatch/category", json={**body, "category": "invalid"}).status_code
            == 422
        )
        assert client.post("/api/dispatch/category", json=body).status_code == 200
        assert main.sim.state["awaiting_plan"]
        assert before == main.sim.state["active_plan"]["movements"]
        trains = scenario_for(main.sim.state).trains
        assert next(t for t in trains if t.id == body["train_id"]).priority == 100
        assert {t.priority for t in trains} == {20, 70, 100}
        state = client.get("/api/state").json()
        assert state["signals"] and all(s["aspect"] == "red" for s in state["signals"])


def test_publish_persists_yield_once_and_retains_after_replan(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from backend.app import main
    from backend.app.storage import Record, Store

    state = example()
    state.update(epoch="journal-test", state_version=1, sim_time_s=110)
    observed = journal_events(state, yield_intervals(state))
    def snapshot():
        return dict(
            epoch=state["epoch"],
            state_version=state["state_version"],
            sim_time_s=state["sim_time_s"],
            dispatch_events=observed,
        )
    store = Store(f"sqlite:///{tmp_path / 'journal.sqlite'}")
    monkeypatch.setattr(main, "sim", SimpleNamespace(state=state, snapshot=snapshot))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "ingestion", None)
    monkeypatch.setattr(main, "deliver_snapshot", lambda *args: None)
    monkeypatch.setattr(main, "last_saved_snapshot", None)
    try:
        main.publish()
        main.publish()
        observed.clear()  # new plan no longer contains the old yielding interval
        state["state_version"] += 1
        main.publish()
        store.flush()
        with Session(store.engine) as session:
            events = session.scalars(select(Record).where(Record.kind == "event")).all()
            assert len(events) == 2
            assert [r.payload["type"] for r in events] == ["yield.started", "yield.finished"]
        assert len(store.history(state["epoch"], 0, 200)[-1]["dispatch_events"]) == 2
    finally:
        store.close()
