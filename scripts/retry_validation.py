"""Retry only timed-out cases, preserving the original result and timing."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from backend.app.corridors import CORRIDORS
from backend.app.metrics.load import calculate_track_load
from backend.app.metrics.quality import MetricConfig, calculate_metrics
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.regional import regional_scenario
from backend.app.regional_traffic import reference_demand
from backend.app.scenarios import corridor_scenario, incident_scenario
from backend.app.station_capacity import expanded_stations
from backend.app.traffic import daily_demand
from backend.app.validation.plan import validate_plan


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--budget", type=float, default=10)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())
    config = MetricConfig.load("config/metrics.json")
    bases = {}
    for row in data["cases"]:
        if row.get("solver_status") != "UNKNOWN" or row["status"] != "no_valid_plan_hold":
            continue
        key, multiplier = row["corridor"], row["multiplier"]
        if (key, multiplier) not in bases:
            scenario = (
                reference_demand(regional_scenario(), multiplier=multiplier)
                if key == "akmola_network"
                else daily_demand(
                    expanded_stations(corridor_scenario(CORRIDORS[key][1])), multiplier=multiplier
                )
            )
            baseline = build_baseline(scenario, time_budget_s=10).plan
            if baseline is None:
                continue
            bases[key, multiplier] = (scenario, baseline)
        scenario, baseline = bases[key, multiplier]
        changed = incident_scenario(scenario, baseline, row["case"])
        started = time.perf_counter()
        result = solve_plan(changed, previous=baseline, time_budget_s=args.budget)
        original = dict(row)
        row["initial_attempt"] = original
        row["retry_budget_s"] = args.budget
        row["solver_status"] = result.status
        row["diagnostics"] = result.diagnostics
        if result.plan is not None:
            errors = validate_plan(changed, result.plan, baseline)
            metrics = calculate_metrics(changed, result.plan, config, previous=baseline)
            load = calculate_track_load(
                changed, result.plan, window_start_s=0, window_end_s=86400, previous=baseline
            )
            row.update(
                status="validated" if not errors and metrics.applicable else "invalid",
                violations=[e.model_dump() for e in errors],
                delay_s=metrics.total_delay_s,
                energy_kwh=metrics.energy_kwh,
                peak_load=max((t.occupancy_fraction for t in load.tracks), default=0),
                completed_in_day=metrics.completed_by_window,
                completion_s=max(m.end_s for m in result.plan.movements),
            )
        row["elapsed_s"] = round(time.perf_counter() - started, 3)
        row["within_5s"] = row["elapsed_s"] <= 5
        print(key, row["case"], multiplier, row["status"], row["elapsed_s"], flush=True)
        data["summary"] = dict(Counter(r["status"] for r in data["cases"]))
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    data["summary"] = dict(Counter(r["status"] for r in data["cases"]))
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    run()
