"""Integration checks for sourced corridor data and synthetic disruption recovery."""

import json
import shutil
import subprocess
import sys

import pytest

from app.advisory.speed import build_speed_profile
from app.metrics.load import calculate_track_load
from app.planning.baseline import build_baseline
from app.planning.solver import solve_plan
from app.scenarios import (
    DEFAULT_INFRASTRUCTURE,
    INCIDENTS,
    PROJECT_ROOT,
    audit_infrastructure,
    corridor_scenario,
    incident_scenario,
    load_infrastructure,
)
from app.validation.plan import validate_plan


@pytest.fixture(scope="module")
def corridor():
    return corridor_scenario()


@pytest.fixture(scope="module")
def original(corridor):
    result = build_baseline(corridor)
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(corridor, result.plan)
    return result.plan


def test_route_is_connected_through_astana_1_to_nurly_zhol():
    infrastructure = load_infrastructure()
    ids = [s["id"] for s in infrastructure["stations"]]
    assert ids[0] == "KOKSHETAU_1"
    assert ids[-2:] == ["ASTANA_1", "NURLY_ZHOL"]
    assert infrastructure["terminal_ids"] == [ids[0], ids[-1]]
    assert [(s["station_a"], s["station_b"]) for s in infrastructure["sections"]] == list(
        zip(ids, ids[1:])
    )
    assert sum(s["length_m"] for s in infrastructure["sections"]) == pytest.approx(
        infrastructure["length_m"], abs=0.1
    )
    assert 310000 < infrastructure["length_m"] < 313000
    source = json.loads(DEFAULT_INFRASTRUCTURE.with_name("sources.json").read_text())
    snapshot = source["snapshot"]
    path = snapshot["path"]
    assert path
    for previous, current in zip(path, path[1:]):
        assert previous[1] == current[0]
    for a, b, way_id in path:
        assert a in snapshot["nodes"] and b in snapshot["nodes"]
        nodes, tags = snapshot["ways"][way_id]
        assert tags["railway"] == "rail"
        assert (a, b) in set(zip(nodes, nodes[1:])) or (b, a) in set(zip(nodes, nodes[1:]))
    for section in infrastructure["sections"]:
        assert all(str(way) in snapshot["ways"] for way in section["osm_way_ids"])


def test_map_observations_are_not_claimed_as_operating_capacity():
    infrastructure = load_infrastructure()
    report = audit_infrastructure(infrastructure)
    assert report["operational_exactness"] is False
    assert all(s["operating_track_count"] is None for s in report["stations"])
    assert all(s["capacity_status"] == "assumed_for_simulation" for s in report["stations"])
    approach, terminal = infrastructure["sections"][-2:]
    assert approach["mapped_main_track_count"] is None
    assert terminal["mapped_main_track_count"] == 4
    assert terminal["model_main_track_count"] == 2
    assert approach["status"] == terminal["status"] == "assumed"
    with pytest.raises(ValueError, match="Точная эксплуатационная модель недоступна"):
        corridor_scenario(require_exact=True)


def test_synthetic_traffic_runs_both_directions_with_separate_freight_terminal(corridor):
    assert corridor.metadata["traffic"]["source"] == "synthetic"
    assert corridor.metadata["operational_exactness"] is False
    assert all(t.id.startswith("SYN-") for t in corridor.trains)
    for kind, terminal in (("passenger", "NURLY_ZHOL"), ("freight", "ASTANA_1")):
        trains = [t for t in corridor.trains if t.kind == kind]
        assert {(t.route[0], t.route[-1]) for t in trains} == {
            ("KOKSHETAU_1", terminal),
            (terminal, "KOKSHETAU_1"),
        }
        if kind == "freight":
            assert all("NURLY_ZHOL" not in t.route for t in trains)
    assert all(s.max_speed_mps <= 80 / 3.6 for s in corridor.sections)


def test_saved_osm_snapshot_rebuilds_without_network(tmp_path):
    directory = DEFAULT_INFRASTRUCTURE.parent
    shutil.copyfile(directory / "sources.json", tmp_path / "sources.json")
    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools/build_corridor_data.py"),
            "--output",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    for filename in ("sources.json", "infrastructure.json", "geometry.geojson"):
        assert (tmp_path / filename).read_bytes() == (directory / filename).read_bytes()


def test_track_load_uses_synthetic_label_and_fixed_comparison_window(corridor, original):
    load = calculate_track_load(corridor, original, window_start_s=0)
    assert load.kind == "synthetic_forecast"
    assert (load.window_start_s, load.window_end_s) == (0, 43200)
    assert len(load.tracks) == 2 * (len(corridor.stations) + len(corridor.sections))
    tracks = [t for t in load.tracks if t.resource_type == "main_track"]
    assert sum(t.train_entries for t in tracks) == len(original.movements)
    assert all(t.mapping_status != "unspecified" for t in load.tracks)
    assert any(t.counts_a_to_b for t in tracks) and any(t.counts_b_to_a for t in tracks)


@pytest.mark.parametrize("kind", INCIDENTS)
def test_each_disruption_invalidates_the_old_plan(corridor, original, kind):
    state = incident_scenario(corridor, original, kind)
    assert state.now_s > 0 and state.state_version == corridor.state_version + 1
    rebound = original.model_copy(update={"state_version": state.state_version})
    violations = validate_plan(state, rebound, original)
    assert violations
    assert all(v.code != "STALE" for v in violations)
    if kind == "ten_incidents":
        assert len(state.blocks) == 10
        assert len({b.resource for b in state.blocks}) == 10
    assert corridor.blocks == []
    assert corridor.now_s == 0


@pytest.mark.parametrize(
    "kind", ["main_track_closure", "single_track_operation", "speed_restriction"]
)
def test_repair_preserves_departed_trains_and_respects_new_limits(corridor, original, kind):
    state = incident_scenario(corridor, original, kind)
    result = solve_plan(state, previous=original, time_budget_s=2)
    assert result.plan is not None, result.diagnostics
    assert not validate_plan(state, result.plan, original)
    repaired = {(m.train_id, m.origin): m for m in result.plan.movements}
    for movement in original.movements:
        if movement.start_s <= state.now_s:
            assert repaired[movement.train_id, movement.origin] == movement
    if kind == "speed_restriction":
        section = next(s for s in state.sections if s.max_speed_mps == 40 / 3.6)
        movement = next(m for m in result.plan.movements if m.section_id == section.id)
        train = next(t for t in state.trains if t.id == movement.train_id)
        profile = build_speed_profile(
            train,
            section,
            target_duration_s=movement.end_s - movement.start_s,
            reverse=movement.origin == section.station_b,
        )
        assert profile.status == "FEASIBLE"
        assert max(p.speed_mps for p in profile.points) <= 40 / 3.6 + 1e-8
        assert profile.duration_s == pytest.approx(movement.end_s - movement.start_s)
