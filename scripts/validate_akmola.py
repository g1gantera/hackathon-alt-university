"""Run all 19 disturbances and 1x/2x/3x day demand; save auditable results."""

import argparse
import csv
import html
import json
import time
from collections import Counter
from pathlib import Path

from backend.app.corridors import CORRIDORS
from backend.app.metrics.load import calculate_track_load
from backend.app.metrics.quality import MetricConfig, calculate_metrics
from backend.app.planning.baseline import build_baseline
from backend.app.planning.solver import solve_plan
from backend.app.scenarios import INCIDENTS, corridor_scenario, incident_scenario
from backend.app.station_capacity import expanded_stations
from backend.app.traffic import daily_demand
from backend.app.validation.plan import validate_plan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/validation"
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = MetricConfig.load(ROOT / "config/metrics.json")


def run():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--cross-load", action="store_true")
    parser.add_argument("--budget", type=float, default=5)
    parser.add_argument("--corridor")
    parser.add_argument("--cases", help="Comma-separated subset for focused regression")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    if args.output_dir:
        OUT = Path(args.output_dir)
        OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    def save():
        result = {
            "date": "2026-10-02",
            "reference_month": "2026-05",
            "solver_budget_s": args.budget,
            "cross_load": args.cross_load,
            "demand_note": "All departure times and freight volumes synthetic; passenger pairs from archived KTZ reference.",
            "summary": dict(Counter(r["status"] for r in rows)),
            "cases": rows,
        }
        (OUT / "akmola-results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        fields = [
            "corridor",
            "case",
            "multiplier",
            "trains",
            "status",
            "elapsed_s",
            "violations",
            "delay_s",
            "energy_kwh",
            "peak_load",
            "diagnostics",
        ]
        with (OUT / "akmola-results.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        cells = "".join(
            "<tr>"
            + "".join("<td>" + html.escape(str(r.get(k, ""))) + "</td>" for k in fields)
            + "</tr>"
            for r in rows
        )
        (OUT / "akmola-results.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>Проверка логики Акмолы</title><style>body{font:14px system-ui;padding:24px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}th{position:sticky;top:0;background:#eee}</style><h1>Нагрузка и 19 сценариев</h1><p>2026-10-02 · '
            + html.escape(str(result["summary"]))
            + "</p><p>Повседневный спрос — модель. Источник пассажирских маршрутов: таблица КТЖ за май 2026. Времена и грузовые рейсы синтетические. Неприменимые сценарии не считаются успешными прогонами.</p><table><thead><tr>"
            + "".join("<th>" + k + "</th>" for k in fields)
            + "</tr></thead><tbody>"
            + cells
            + "</tbody></table>"
        )

    for key, (_, path) in CORRIDORS.items():
        if args.corridor and key != args.corridor:
            continue
        base = None if key == "akmola_network" else expanded_stations(corridor_scenario(path))
        for multiplier in (1, 2, 3):
            if key == "akmola_network":
                from backend.app.regional import regional_scenario
                from backend.app.regional_traffic import reference_demand

                scenario = reference_demand(regional_scenario(), multiplier=multiplier)
            else:
                scenario = daily_demand(base, multiplier=multiplier)
            began = time.perf_counter()
            baseline = build_baseline(scenario, time_budget_s=max(5, args.budget))
            if baseline.plan is None:
                rows.append(
                    dict(
                        corridor=key,
                        case="daily_load",
                        multiplier=multiplier,
                        trains=len(scenario.trains),
                        status="baseline_unavailable",
                        elapsed_s=round(time.perf_counter() - began, 3),
                        diagnostics=baseline.diagnostics,
                    )
                )
                save()
                continue
            workloads = [("daily_load", scenario, None)]
            if multiplier == 1 or args.cross_load:
                for kind in INCIDENTS:
                    if args.cases and kind not in args.cases.split(","):
                        continue
                    try:
                        workloads.append(
                            (kind, incident_scenario(scenario, baseline.plan, kind), baseline.plan)
                        )
                    except ValueError as error:
                        rows.append(
                            dict(
                                corridor=key,
                                case=kind,
                                multiplier=multiplier,
                                trains=len(scenario.trains),
                                status="not_applicable",
                                diagnostics=str(error),
                            )
                        )
            for kind, changed, previous in workloads:
                if args.cases and kind not in args.cases.split(","):
                    continue
                started = time.perf_counter()
                solved = solve_plan(changed, previous=previous, time_budget_s=args.budget)
                plan = solved.plan
                # A valid baseline may be retained for ordinary demand only.
                retained = False
                if plan is None and previous is None:
                    plan = baseline.plan
                    retained = True
                row = dict(
                    corridor=key,
                    case=kind,
                    multiplier=multiplier,
                    trains=len(scenario.trains),
                    solver_status=solved.status,
                    diagnostics=solved.diagnostics,
                    baseline_retained=retained,
                )
                if plan is None:
                    row.update(status="no_valid_plan_hold", violations=None)
                else:
                    errors = validate_plan(changed, plan, previous)
                    metrics = calculate_metrics(changed, plan, CONFIG, previous=previous)
                    load = calculate_track_load(
                        changed, plan, window_start_s=0, window_end_s=86400, previous=previous
                    )
                    row.update(
                        status="validated" if not errors and metrics.applicable else "invalid",
                        violations=[e.model_dump() for e in errors],
                        delay_s=metrics.total_delay_s,
                        energy_kwh=metrics.energy_kwh,
                        peak_load=max((t.occupancy_fraction for t in load.tracks), default=0),
                        completed_in_day=metrics.completed_by_window,
                        completion_s=max(m.end_s for m in plan.movements),
                    )
                row["elapsed_s"] = round(time.perf_counter() - started, 3)
                row["within_5s"] = row["elapsed_s"] <= 5
                rows.append(row)
                save()
                print(key, kind, multiplier, row["status"], row["elapsed_s"], flush=True)
    save()
    print("SUMMARY", dict(Counter(r["status"] for r in rows)), flush=True)


if __name__ == "__main__":
    run()
