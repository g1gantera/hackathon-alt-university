import json

import pytest

from backend.app.metrics.load import calculate_track_load
from backend.app.planning.baseline import build_baseline
from backend.app.schemas import Block, MainTrack


def two_tracks(small):
    scenario = small.model_copy(deep=True)
    scenario.sections[0].main_tracks = [
        MainTrack(id="1", direction="a_to_b"),
        MainTrack(id="2", direction="b_to_a"),
    ]
    return scenario


def planned(scenario):
    result = build_baseline(scenario)
    assert result.plan is not None, result.diagnostics
    return result.plan


def by_id(report):
    return {track.resource_id: track for track in report.tracks}


def test_union_of_overlapping_closures_is_clipped_and_path_specific(small):
    scenario = two_tracks(small)
    scenario.blocks = [
        Block(id="all", resource="section:AB", start_s=0, end_s=100),
        Block(id="one", resource="main_track:AB:1", start_s=50, end_s=200),
    ]
    report = by_id(calculate_track_load(scenario, planned(scenario), 25, 175))
    first, second = report["main_track:AB:1"], report["main_track:AB:2"]
    assert (first.closed_s, first.available_s, first.occupied_s) == (150, 0, 0)
    assert first.utilization_of_available is None
    assert (second.closed_s, second.available_s, second.occupied_s) == (75, 75, 75)
    assert second.occupancy_fraction == 0.5
    assert second.utilization_of_available == 1
    assert report["track:A:1"].closed_s == 0


def test_individual_station_closure_does_not_close_other_resources(small):
    scenario = two_tracks(small)
    scenario.blocks = [Block(id="station", resource="track:A:1", start_s=0, end_s=4000)]
    report = by_id(calculate_track_load(scenario, planned(scenario), 0, 1000))
    assert report["track:A:1"].available_s == 0
    assert report["track:A:1"].occupied_s == 0
    assert report["track:A:2"].available_s == 1000
    assert report["main_track:AB:1"].available_s == 1000
    assert report["main_track:AB:2"].available_s == 1000


def test_signal_counts_entry_blocking_without_subtracting_available_time(small):
    scenario = two_tracks(small)
    scenario.blocks = [
        Block(id="all", resource="section:AB", start_s=0, end_s=100, kind="signal"),
        Block(id="one", resource="main_track:AB:1", start_s=50, end_s=200, kind="signal"),
    ]
    report = by_id(calculate_track_load(scenario, planned(scenario), 25, 175))
    first, second = report["main_track:AB:1"], report["main_track:AB:2"]
    assert first.entry_blocked_s == 150
    assert second.entry_blocked_s == 75
    assert first.available_s == second.available_s == 150
    assert first.closed_s == second.closed_s == 0


def test_unused_tracks_are_present_and_have_zero_utilization(small):
    scenario = two_tracks(small)
    scenario.sections[0].main_tracks.append(MainTrack(id="unused"))
    report = by_id(calculate_track_load(scenario, planned(scenario)))
    unused = report["main_track:AB:unused"]
    assert unused.occupied_s == unused.train_entries == 0
    assert unused.counts_a_to_b == unused.counts_b_to_a == 0
    assert unused.occupancy_fraction == unused.utilization_of_available == 0
    assert report["track:A:2"].train_entries == 0


def test_direction_counts_and_entry_boundaries(small):
    scenario = two_tracks(small)
    plan = planned(scenario)
    report = by_id(calculate_track_load(scenario, plan, 20, 21))
    first, second = report["main_track:AB:1"], report["main_track:AB:2"]
    assert (first.train_entries, first.counts_a_to_b, first.counts_b_to_a) == (1, 1, 0)
    assert (second.train_entries, second.counts_a_to_b, second.counts_b_to_a) == (1, 0, 1)
    assert first.occupied_s == second.occupied_s == 1
    before = by_id(calculate_track_load(scenario, plan, 0, 20))
    assert before["main_track:AB:1"].train_entries == 0
    after = by_id(calculate_track_load(scenario, plan, 21, 22))
    assert after["main_track:AB:1"].train_entries == 0
    assert after["main_track:AB:1"].occupied_s == 1


def test_clearance_is_included_and_clipped(small):
    scenario = two_tracks(small)
    plan = planned(scenario)
    forward = next(m for m in plan.movements if m.origin == "A")
    report = by_id(calculate_track_load(scenario, plan, forward.end_s, forward.end_s + 60))
    # 100 m passenger train / 5 m/s clearance + 10 s buffer.
    assert report["main_track:AB:1"].occupied_s == 30
    assert report["main_track:AB:1"].occupancy_fraction == 0.5
    assert report["main_track:AB:1"].train_entries == 0


def test_invalid_or_stale_plan_is_rejected_before_scoring(small):
    scenario = two_tracks(small)
    plan = planned(scenario)
    plan.movements[1].main_track_id = "1"
    with pytest.raises(ValueError, match="Cannot calculate load for invalid plan"):
        calculate_track_load(scenario, plan)
    plan = planned(scenario)
    plan.state_version += 1
    with pytest.raises(ValueError, match="STALE_PLAN"):
        calculate_track_load(scenario, plan)


@pytest.mark.parametrize("bounds", [(-1, 5), (10, 10), (20, 10), (0, 4001), (0.5, 10), (0, True)])
def test_invalid_window_is_rejected(small, bounds):
    with pytest.raises(ValueError, match="Load window"):
        calculate_track_load(small, planned(small), *bounds)


def test_mapping_and_synthetic_provenance_are_serializable(small):
    scenario = two_tracks(small)
    scenario.metadata = {
        "traffic": {"source": "synthetic"},
        "mapping_evidence": {
            "section:AB": {"status": "mapped", "source_url": "https://example.test/map"},
            "main_track:AB:2": "assumed_direction",
            "station:A": {"status": "unverified_capacity"},
        },
    }
    report = calculate_track_load(scenario, planned(scenario))
    rows = by_id(report)
    assert report.kind == "synthetic_forecast"
    assert rows["main_track:AB:1"].mapping_status == "mapped"
    assert rows["main_track:AB:2"].mapping_status == "assumed_direction"
    assert rows["track:A:1"].mapping_status == "unverified_capacity"
    assert rows["track:B:1"].mapping_status == "unspecified"
    assert rows["main_track:AB:1"].label == "A — B · главный путь 1"
    payload = json.loads(report.model_dump_json())
    assert payload["occupation_basis"] == "reservations_including_clearance"
    scenario.metadata = {"traffic": {"source": "actual"}}
    assert calculate_track_load(scenario, planned(scenario)).kind == "forecast"
