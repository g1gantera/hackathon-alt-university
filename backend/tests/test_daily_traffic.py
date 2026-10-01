from backend.app.planning.baseline import build_baseline
from backend.app.scenarios import corridor_scenario
from backend.app.station_capacity import expanded_stations
from backend.app.traffic import daily_demand
from backend.app.validation.plan import validate_plan


def test_expanded_paths_are_assumptions_and_are_real_planner_resources():
    original = corridor_scenario()
    scenario = expanded_stations(original)
    assert len(original.stations[0].tracks) == 2
    assert len(scenario.stations[0].tracks) == 4
    assert len(scenario.stations[-1].tracks) == 6
    assert not scenario.metadata["station_capacity"]["verified_by_operator"]
    scenario.trains[0].manual_station_tracks = {scenario.stations[0].id: "SIM-4"}
    plan = build_baseline(scenario, time_budget_s=5).plan
    assert plan is not None and not validate_plan(scenario, plan)
    assert (
        next(
            s.track_id
            for s in plan.stops
            if s.train_id == scenario.trains[0].id and s.station_id == scenario.stations[0].id
        )
        == "SIM-4"
    )


def test_day_load_preserves_reference_provenance_and_synthesizes_times():
    base = expanded_stations(corridor_scenario())
    friday = daily_demand(base, "2026-10-02")
    tuesday = daily_demand(base, "2026-10-06")
    assert len(friday.trains) == 14 and len(tuesday.trains) == 12
    assert sum(t.kind == "freight" for t in friday.trains) == 8
    metadata = friday.metadata["traffic"]
    assert metadata["reference_month"] == "2026-05" and not metadata["verified_for_current_date"]
    assert all(r["departure_time_source"] == "synthetic" for r in metadata["records"].values())
    assert any(r.get("reference_pair") == "887/888" for r in metadata["records"].values())
    assert not any(
        r.get("reference_pair") == "887/888"
        for r in tuesday.metadata["traffic"]["records"].values()
    )
    high = daily_demand(base, multiplier=3)
    assert len(high.trains) == 42 and len({t.id for t in high.trains}) == 42
    assert all(0 <= t.release_s < 86400 and t.due_s > t.release_s for t in high.trains)


def test_holiday_rule_is_explicit_and_synthetic_freight_is_retained():
    base = corridor_scenario("data/akmola/atbasar_esil.json")
    ordinary = daily_demand(base, holiday=False)
    holiday = daily_demand(base, holiday=True)
    assert any(
        r.get("reference_pair") == "6889/6890"
        for r in ordinary.metadata["traffic"]["records"].values()
    )
    assert not any(r.get("reference_pair") for r in holiday.metadata["traffic"]["records"].values())
    assert sum(t.kind == "freight" for t in holiday.trains) == 8
