"""Execute the original stochastic model and export constraints for the CP-SAT planner."""

import dataclasses
import json
from pathlib import Path

import pandas as pd
from railsim.economics import attribution_table, summary_table
from railsim.hazards import (
    apply_hazards,
    apply_incidents,
    apply_maintenance,
    generate_raw_hazards,
    generate_raw_incidents,
)
from railsim.simulation import _rngs, run_scenario
from railsim.trains import generate_schedule, traffic_histogram
from railsim.util import Scenario as SourceScenario

from app.realism.configuration import build_config, load_profile
from app.realism.incidents import ConstraintRecorder, apply_recorded_constraints
from app.schemas import DavisResistance, Scenario


def _json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def _union_hours(intervals, end_time):
    cursor, total = 0.0, 0.0
    for start, end, *_ in sorted(intervals):
        start, end = max(0.0, start), min(end, end_time)
        if end > start:
            total += max(0.0, end - max(start, cursor))
            cursor = max(cursor, end)
    return total


def export_world(base, cfg, source_scenario, run_index=0):
    """Shared raw world, plus policy-specific known constraints for offline replay."""
    seed = cfg.RANDOM_SEED + run_index
    rngs = _rngs(seed)
    horizon = cfg.SIM_DAYS * 24.0
    hazards = generate_raw_hazards(cfg, rngs["hazards"], horizon)
    incidents = generate_raw_incidents(cfg, rngs["incidents"], horizon)
    trains = generate_schedule(cfg, rngs["schedule"])
    recorders = [ConstraintRecorder() for _ in base.sections]
    apply_hazards(recorders, hazards, cfg, source_scenario)
    apply_incidents(recorders, incidents, cfg, source_scenario)
    # apply_maintenance only requires each recorder's index and emission methods.
    for index, recorder in enumerate(recorders):
        recorder.idx = index
    apply_maintenance(recorders, cfg, source_scenario, traffic_histogram(cfg, source_scenario))
    state = base.model_copy(deep=True)
    state.state_version += 1
    apply_recorded_constraints(state, recorders, prefix=f"railsim:{seed}:{source_scenario.name}")
    state.metadata["railsim_replay"] = {
        "seed": seed,
        "policy": source_scenario.name,
        "interpretation": "Offline replay of known intervals, not live foresight or a safety guarantee",
        "hidden_hazard_windows": [r.hazard_windows for r in recorders],
        "vehicle_failures": "Realized in stochastic execution; deterministic examples are separate railsim_* incidents",
    }
    world = {
        "seed": seed,
        "hazards": [dataclasses.asdict(h) for h in hazards],
        "incidents": [dataclasses.asdict(i) for i in incidents],
        "trains": [
            {
                "source_id": t.id,
                "corridor_train_id": t.type_name,
                "departure_h": t.sched_dep,
                "direction": t.direction,
                "origin": t.origin,
                "destination": t.dest,
                "u_loco": t.u_loco.tolist(),
                "u_wagon": t.u_wagon.tolist(),
                "u_wagon_det": t.u_wagon_det.tolist(),
            }
            for t in trains
        ],
    }
    return world, Scenario.model_validate(state.model_dump())


def run_realism(
    base, output, *, profile_path=None, runs=None, seed=None, plots=False, attribution=False
):
    profile = load_profile() if profile_path is None else load_profile(profile_path)
    cfg = build_config(base, profile, runs=runs, seed=seed)
    base = base.model_copy(deep=True)
    for train in base.trains:
        train.davis_resistance = DavisResistance(
            a=cfg.RESISTANCE_A, b=cfg.RESISTANCE_B, c=cfg.RESISTANCE_C
        )
        train.traction_efficiency = cfg.ELECTRIC_EFFICIENCY
    for section, spec in zip(base.sections, cfg.SEGMENTS, strict=True):
        section.grade_permille = spec["grade_permille"]
    base.metadata["realism"].update(
        calibration_status=profile["calibration_status"],
        note=profile["note"],
        replay_energy_model="electrical profiles; source diesel totals stay in the stochastic report",
    )
    base = Scenario.model_validate(base.model_dump())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    features = {k for k, enabled in cfg.AUTO_FEATURES.items() if enabled}
    policies = [SourceScenario("baseline"), SourceScenario("auto", features)]
    if attribution:
        policies += [SourceScenario(f"auto:{feature}", {feature}) for feature in sorted(features)]
    rows, details = [], {}
    for run in range(cfg.N_RUNS):
        for policy in policies:
            kpi, detail = run_scenario(cfg, policy, run)
            kpi.update(
                kind="synthetic_stochastic_comparison",
                calibration_status=profile["calibration_status"],
            )
            if kpi["trains_total"] < 5:
                raise ValueError("A simulation run generated fewer than five trains")
            # Keep the source result visible, but correct overlapping closures in exported KPI.
            kpi["closure_hours_raw_sum"] = kpi["closure_hours"]
            kpi["closure_hours"] = sum(
                _union_hours(s.closures, cfg.SIM_DAYS * 24 + cfg.DRAIN_HOURS)
                for s in detail["segments"]
            )
            rows.append(kpi)
            if run == 0:
                details[policy.name] = detail
        print(
            f"railsim: прогон {run + 1}/{cfg.N_RUNS}, поездов {rows[-1]['trains_total']}",
            flush=True,
        )
    df = pd.DataFrame(rows)
    df.to_csv(output / "runs.csv", index=False)
    main_df = df[df["scenario"].isin(["baseline", "auto"])]
    summary_table(main_df).to_csv(output / "summary.csv")
    if attribution:
        attribution_table(df).to_csv(output / "attribution.csv", index=False)
    _json(output / "configuration.json", vars(cfg))
    # A runnable source-format config, keeping original main.py and optional modules usable.
    (output / "configuration.py").write_text(
        "# Generated illustrative railsim configuration, not operator data.\n"
        + "\n".join(f"{k} = {v!r}" for k, v in vars(cfg).items())
        + "\n",
        encoding="utf-8",
    )
    worlds = []
    for policy in policies[:2]:
        world, replay = export_world(base, cfg, policy)
        worlds.append(world)
        _json(output / f"{policy.name}_constraints.json", replay.model_dump(mode="json"))
    if worlds[0] != worlds[1]:
        raise AssertionError("Baseline and auto must use exactly the same stochastic inputs")
    _json(output / "shared_events.json", worlds[0])
    paired = main_df.pivot(index="run", columns="scenario", values="total_cost_kzt")
    savings = paired["baseline"] - paired["auto"]
    report = {
        "kind": "synthetic_stochastic_comparison",
        "corridor": base.metadata["corridor"],
        "minimum_trains": 5,
        "trains_per_run": int(main_df.trains_total.min()),
        "runs": cfg.N_RUNS,
        "seed": cfg.RANDOM_SEED,
        "features": sorted(features),
        "operational_exactness": False,
        "calibration_status": profile["calibration_status"],
        "assumptions": profile["note"],
        "raw_events_identical": True,
        "mean_kpi": main_df.groupby("scenario")
        .mean(numeric_only=True)
        .drop(columns="run")
        .to_dict(orient="index"),
        "paired_model_cost_difference_kzt": {
            "mean": float(savings.mean()),
            "sample_std": float(savings.std(ddof=1)) if cfg.N_RUNS > 1 else None,
            "interpretation": "Illustrative period cost difference, not measured profit or annual ROI",
        },
        "limitations": [
            "Original railsim stations have unlimited capacity; CP-SAT replay still checks finite station tracks.",
            "Original automatic blocking is a count of slots, not block-by-block train separation; default is semi_auto.",
            "Source speed advisory smooths stops in accounting; it does not rebuild the executed trajectory.",
            "Source traction estimate and physical CP-SAT profiles are separate models; their energy totals are not added.",
            "Undetected hazards are simulated risks, not permission to issue a safe movement plan.",
            "Core replay exports known closures and temporary speeds; source-only port/CIS modules are disabled here.",
            "Annual economics from the source is not extrapolated from an illustrative short run.",
        ],
    }
    if cfg.USE_WAGON_MODULE:
        from railsim.wagons import empty_wagon_distribution

        report["empty_wagons"] = empty_wagon_distribution(cfg)
    _json(output / "report.json", report)
    traces = {
        name: [
            {
                "train_id": t.type_name,
                "source_id": t.id,
                "finished": t.finished,
                "trace_hours_km": t.trace,
                "loco_failures": t.loco_failures,
                "wagon_setouts": t.wagon_setouts,
                "derailments": t.derailments,
                "planned_stops": t.planned_stops,
                "unplanned_stops": t.unplanned_stops,
                "smoothed_stops_estimate": t.smoothed_stops,
            }
            for t in detail["trains"]
        ]
        for name, detail in details.items()
    }
    _json(output / "trajectories.json", traces)
    if plots:
        from types import SimpleNamespace

        from railsim.plots import kpi_bars, string_diagram

        names = {s.id: s.name for s in base.stations}
        display_stations = []
        for s in cfg.STATIONS:
            # Thin adjacent axis labels only; all station positions stay in train traces.
            if s["name"] == "RAZYEZD_39":
                continue
            display_stations.append(
                {**s, "name": names[s["name"]].replace(" (Курорт Боровое)", "")}
            )
        display_cfg = SimpleNamespace(
            **{
                **vars(cfg),
                "STATIONS": display_stations,
                "TRAIN_TYPES": {"Пассажирские": {}, "Грузовые": {}},
            }
        )
        display_details = {
            name: {
                **detail,
                "trains": [
                    dataclasses.replace(
                        t, type_name="Пассажирские" if t.spec["passenger"] else "Грузовые"
                    )
                    for t in detail["trains"]
                ],
            }
            for name, detail in details.items()
            if name in ("baseline", "auto")
        }
        string_diagram(
            display_details,
            display_cfg,
            output / "string_diagram.png",
            min(
                cfg.PLOT_HOURS,
                max(t.t_line_end or cfg.PLOT_HOURS for d in details.values() for t in d["trains"])
                + 1,
            ),
        )
        kpi_bars(main_df, output / "kpi.png")
    return report
