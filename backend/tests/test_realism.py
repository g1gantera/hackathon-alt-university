import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from railsim.energy import traction_kwh
from railsim.hazards import RawHazard, apply_hazards
from railsim.util import Scenario as SourceScenario

from backend.app.advisory.speed import build_speed_profile, duration_windows, section_at_entry
from backend.app.metrics.load import calculate_track_load
from backend.app.metrics.quality import profile_plan
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.realism.configuration import ROOT, build_config, load_profile
from backend.app.realism.incidents import ConstraintRecorder, apply_recorded_constraints
from backend.app.realism.service import export_world, run_realism
from backend.app.scenarios import corridor_scenario
from backend.app.schemas import DavisResistance, EntrySpeedLimit, MainTrack
from backend.app.validation.plan import validate_plan


@pytest.fixture(scope="module")
def corridor():
    return corridor_scenario(train_count=5)


def test_all_sixteen_source_files_preserved():
    manifest = json.loads((ROOT / "data/railsim/source_manifest.json").read_text())
    assert manifest["reviewed_files"] == 16
    for item in manifest["files"]:
        path = ROOT / "vendor/railsim" / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("count", [0, 1, 4, 41])
def test_corridor_requires_at_least_five_and_at_most_forty(count):
    with pytest.raises(ValueError, match="5..40"):
        corridor_scenario(train_count=count)


def test_cli_rejects_four_trains():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "generate", "--trains", "4"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "5 до 40" in result.stderr


def test_config_units_and_shared_random_inputs(corridor):
    cfg = build_config(corridor)
    assert cfg.STATIONS[-1]["km"] == pytest.approx(311.82134, abs=1e-4)
    assert cfg.TRAIN_TYPES["SYN-F03"]["mass_t"] == 2000
    assert cfg.TRAIN_TYPES["SYN-F03"]["max_speed_kmh"] == pytest.approx(60)
    baseline, base_constraints = export_world(corridor, cfg, SourceScenario("baseline"))
    auto, auto_constraints = export_world(
        corridor, cfg, SourceScenario("auto", {"maintenance_windows"})
    )
    assert baseline == auto
    assert len(baseline["trains"]) == 5
    assert {t["direction"] for t in baseline["trains"]} == {-1, 1}
    assert baseline != export_world(corridor, cfg, SourceScenario("baseline"), run_index=1)[0]
    assert base_constraints.blocks != auto_constraints.blocks
    assert not cfg.USE_BORDER and not cfg.USE_PORT and not cfg.USE_CIS_EXCHANGE
    assert all(
        not cfg.AUTO_FEATURES[f]
        for f in ("hazard_monitoring", "predictive_maintenance", "wagon_detectors")
    )
    assert cfg.ECO_DRIVING_SAVING["auto"] == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("ELECTRIC_EFFICIENCY", 0),
        ("BLOCK_SECTION_KM", 0),
        ("LOCO_RESCUE_H", -1),
        ("N_RUNS", 0),
        ("RESISTANCE_A", float("nan")),
    ],
)
def test_invalid_source_parameters_rejected(corridor, key, value):
    profile = load_profile()
    profile["parameters"][key] = value
    with pytest.raises(ValueError):
        build_config(corridor, profile)


def test_temporal_speeds_use_half_open_entry_windows(small):
    train, section = small.trains[0], small.sections[0]
    section.entry_speed_limits = [
        EntrySpeedLimit(id="snow", start_s=100, end_s=200, speed_factor=0.5, reason="snow"),
        EntrySpeedLimit(id="heat", start_s=150, end_s=250, speed_factor=0.7, reason="heat"),
    ]
    assert section_at_entry(train, section, 99).max_speed_mps == 20
    assert section_at_entry(train, section, 100).max_speed_mps == 10
    assert section_at_entry(train, section, 199).max_speed_mps == 10
    assert section_at_entry(train, section, 200).max_speed_mps == 14
    assert section_at_entry(train, section, 250).max_speed_mps == 20
    windows = duration_windows(train, section, "A", small.horizon_s)
    assert windows[0][0] == 0 and windows[-1][1] == small.horizon_s + 1
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_solver_can_wait_for_speed_restriction_to_expire(small):
    small.trains = small.trains[:1]
    small.sections[0].entry_speed_limits = [
        EntrySpeedLimit(id="snow", start_s=0, end_s=200, speed_factor=0.05, reason="snow")
    ]
    result = solve_plan(small, time_budget_s=1)
    assert result.plan is not None, result.diagnostics
    assert result.plan.movements[0].start_s >= 200
    assert not validate_plan(small, result.plan)
    assert (
        max(p.speed_mps for p in next(iter(profile_plan(small, result.plan).values())).points) > 10
    )


def test_fcfs_and_profiles_obey_an_active_weather_limit(small):
    small.sections[0].entry_speed_limits = [
        EntrySpeedLimit(
            id="snow", start_s=0, end_s=small.horizon_s, speed_factor=0.5, reason="snow"
        )
    ]
    result = build_baseline(small)
    assert result.plan is not None and not validate_plan(small, result.plan)
    profiles = profile_plan(small, result.plan)
    for train in small.trains:
        assert (
            max(p.speed_mps for p in profiles[f"{train.id}:AB"].points) <= train.max_speed_mps * 0.5
        )


def test_recovery_occupies_both_tracks_without_turning_hold_into_slow_motion(small):
    section = small.sections[0]
    section.main_tracks = [
        MainTrack(id="1", direction="a_to_b"),
        MainTrack(id="2", direction="b_to_a"),
    ]
    small.trains[0].section_hold_s = {"AB": 180}
    small.trains[0].section_recovery_all_tracks = ["AB"]
    for result in (build_baseline(small), solve_plan(small, time_budget_s=1)):
        assert result.plan is not None, result.diagnostics
        assert not validate_plan(small, result.plan)
        movement = next(m for m in result.plan.movements if m.train_id == "P1")
        profile = profile_plan(small, result.plan)["P1:AB"]
        assert profile.stationary_hold_s == 180
        assert profile.points[0].position_m == profile.points[1].position_m == 0
        assert profile.points[1].time_s == 180
        assert profile.points[1].speed_mps == 0
        assert profile.duration_s == pytest.approx(movement.end_s - movement.start_s)
        load = calculate_track_load(small, result.plan)
        peer_track = next(t for t in load.tracks if t.resource_id == "main_track:AB:2")
        assert peer_track.closed_s == 180
        bad = result.plan.model_copy(deep=True)
        peer = next(m for m in bad.movements if m.train_id == "F1")
        peer.start_s, peer.end_s = movement.start_s, movement.start_s + 90
        assert any(e.code == "RECOVERY_CONFLICT" for e in validate_plan(small, bad))


def test_davis_work_matches_source_formula_and_grade_reverses(small):
    train, section = small.trains[0], small.sections[0]
    train.davis_resistance = DavisResistance(a=1.5, b=0.01, c=0.00035)
    train.auxiliary_power_w = 0
    section.grade_permille = 2
    cfg = SimpleNamespace(RESISTANCE_A=1.5, RESISTANCE_B=0.01, RESISTANCE_C=0.00035)
    forward = build_speed_profile(train, section, initial_speed_mps=20, final_speed_mps=20)
    reverse = build_speed_profile(
        train, section, reverse=True, initial_speed_mps=20, final_speed_mps=20
    )
    assert forward.energy_kwh == pytest.approx(
        traction_kwh(cfg, train.mass_kg / 1000, 72, section.length_m / 1000, 2)
        / train.traction_efficiency
    )
    assert reverse.energy_kwh < forward.energy_kwh


def test_source_weather_ends_naturally_and_defect_repair_starts_after_detection(corridor):
    cfg = build_config(corridor)
    cfg.DETECTION_PROB["baseline"]["rail_defect"] = 0
    cfg.DETECTION_PROB["baseline"]["snow"] = 0
    records = [ConstraintRecorder() for _ in corridor.sections]
    apply_hazards(
        records,
        [RawHazard(1, "rail_defect", 0, 1, 2, 0.9), RawHazard(2, "snow", 1, 1, 2, 0.9)],
        cfg,
        SourceScenario("baseline"),
    )
    assert records[0].closures[0][:2] == (3, 5)
    assert records[1].restrictions[0][:2] == (1.5, 3)
    state = corridor.model_copy(deep=True)
    apply_recorded_constraints(state, records, prefix="test")
    assert state.blocks[0].start_s == 10800
    assert state.blocks[0].end_s == 18000
    assert state.sections[1].entry_speed_limits[0].end_s == 10800
    assert records[0].hazard_windows[0][:2] == (1, 3)


def test_stochastic_run_exports_five_trains_and_replay(corridor, tmp_path):
    report = run_realism(corridor, tmp_path, runs=1, seed=42)
    assert report["trains_per_run"] == 5
    assert report["raw_events_identical"] is True
    assert report["calibration_status"] == "illustrative"
    assert report["paired_model_cost_difference_kzt"]["sample_std"] is None
    assert (tmp_path / "baseline_constraints.json").is_file()
    assert (tmp_path / "auto_constraints.json").is_file()
    assert len(json.loads((tmp_path / "shared_events.json").read_text())["trains"]) == 5
