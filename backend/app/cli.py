"""CLI for participant 3: corridor data, planning, advisory and forecast metrics."""

import argparse
import csv
import json
import sys
from pathlib import Path

from backend.app.metrics.economics import EconomicsRates, compare_plan_costs
from backend.app.metrics.quality import MetricConfig, calculate_metrics
from backend.app.planning.baseline import build_baseline
from backend.app.planning.service import plan_alternatives
from backend.app.planning.solver import solve_plan
from backend.app.realism.incidents import LABELS as REALISM_LABELS
from backend.app.realism.service import run_realism
from backend.app.scenarios import (
    DEFAULT_INFRASTRUCTURE,
    INCIDENTS,
    audit_infrastructure,
    corridor_scenario,
    incident_scenario,
    load_infrastructure,
)
from backend.app.schemas import Plan, Scenario
from backend.app.validation.plan import summarize_violations, validate_plan

INCIDENT_LABELS = {
    "normal": "Исходное движение",
    "main_track_closure": "Закрытие одного главного пути",
    "signal_failure": "Неисправность сигнала",
    "station_track_closure": "Занятый станционный путь",
    "train_delay": "Задержка поезда на 30 минут",
    "speed_restriction": "Ограничение скорости до 40 км/ч",
    "single_track_operation": "Движение по одному пути с явным разрешением обоих направлений",
    "ten_incidents": "Десять одновременных ограничений",
    **{f"railsim_{kind}": label for kind, label in REALISM_LABELS.items()},
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def _load_rows(metrics):
    return (
        [
            {
                "kind": metrics.track_load.kind,
                "scenario_id": metrics.track_load.scenario_id,
                "state_version": metrics.track_load.state_version,
                "window_start_s": metrics.track_load.window_start_s,
                "window_end_s": metrics.track_load.window_end_s,
                "occupation_basis": metrics.track_load.occupation_basis,
                "resource": t.resource_id,
                "label": t.label,
                "mapping_status": t.mapping_status,
                "occupied_s": t.occupied_s,
                "closed_s": t.closed_s,
                "available_s": t.available_s,
                "entry_blocked_s": t.entry_blocked_s,
                "load_pct": round(t.occupancy_fraction * 100, 2),
                "available_load_pct": None
                if t.utilization_of_available is None
                else round(t.utilization_of_available * 100, 2),
                "entries": t.train_entries,
                "a_to_b": t.counts_a_to_b,
                "b_to_a": t.counts_b_to_a,
            }
            for t in metrics.track_load.tracks
        ]
        if metrics.track_load
        else []
    )


def _summary_row(name, strategy, metrics, elapsed_ms, changed=0):
    return {
        "scenario": name,
        "strategy": strategy,
        "kind": metrics.kind,
        "delay_s": metrics.total_delay_s,
        "passenger_delay_s": metrics.passenger_delay_s,
        "energy_kwh": round(metrics.energy_kwh, 3) if metrics.energy_kwh is not None else None,
        "quality_index": metrics.quality_index,
        "conflicts": metrics.conflict_count,
        "applicable": metrics.applicable,
        "changed_movements": changed,
        "elapsed_ms": round(elapsed_ms, 1),
    }


def _candidate_payload(candidate, export_profiles):
    payload = candidate.model_dump(mode="json", exclude={"profiles"})
    if export_profiles:
        payload["profiles"] = {k: v.model_dump(mode="json") for k, v in candidate.profiles.items()}
    else:
        payload["profile_summaries"] = {
            k: {
                "status": p.status,
                "duration_s": p.duration_s,
                "energy_kwh": p.energy_kwh,
                "stationary_hold_s": p.stationary_hold_s,
                "max_speed_kmh": max((point.speed_mps for point in p.points), default=0) * 3.6,
            }
            for k, p in candidate.profiles.items()
        }
    return payload


def run_simulation(args, config):
    base = corridor_scenario(
        args.infrastructure,
        train_count=args.trains,
        departure_interval_s=args.interval,
        require_exact=args.require_exact,
    )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    print(f"{base.metadata['corridor']} · {len(base.trains)} синтетических поездов", flush=True)
    print(
        "Геометрия: OSM. Станционная вместимость и эксплуатационные режимы: допущения.", flush=True
    )
    write_json(output / "infrastructure_audit.json", base.metadata["audit"])
    write_json(output / "normal/scenario.json", base)
    baseline = build_baseline(base, time_budget_s=min(2, args.budget))
    write_json(output / "fcfs.json", baseline)
    rows = []
    if baseline.plan:
        metrics = calculate_metrics(base, baseline.plan, config)
        rows.append(_summary_row("normal", "fcfs", metrics, baseline.elapsed_ms))
        write_json(output / "fcfs_metrics.json", metrics)
    initial = plan_alternatives(
        base, config, time_budget_s=args.budget, strategies=tuple(args.strategies)
    )
    write_json(
        output / "normal/result.json",
        {
            "elapsed_ms": initial.elapsed_ms,
            "budget_exceeded": initial.budget_exceeded,
            "diagnostics": initial.diagnostics,
            "candidates": [_candidate_payload(c, args.profiles) for c in initial.candidates],
        },
    )
    if not initial.candidates:
        print("Не найден исходный допустимый план:", initial.diagnostics)
        return 2
    previous = initial.candidates[0].plan
    write_json(output / "normal/active_plan.json", previous)
    for candidate in initial.candidates:
        rows.append(
            _summary_row("normal", candidate.plan.strategy, candidate.metrics, initial.elapsed_ms)
        )
        write_csv(
            output / "normal" / f"{candidate.plan.strategy}_track_load.csv",
            _load_rows(candidate.metrics),
        )
    print(
        f"Исходное движение: {len(initial.candidates)} вариантов, {initial.elapsed_ms:.0f} мс",
        flush=True,
    )
    failures = []
    for name in args.incidents:
        scenario = incident_scenario(base, previous, name)
        # Rebind only the version for counterfactual validation; retain every old time/path.
        old_in_new_state = previous.model_copy(update={"state_version": scenario.state_version})
        violations = validate_plan(scenario, old_in_new_state, previous)
        alternatives = plan_alternatives(
            scenario,
            config,
            previous=previous,
            time_budget_s=args.budget,
            strategies=tuple(args.strategies),
        )
        folder = output / name
        write_json(folder / "scenario.json", scenario)
        write_json(folder / "previous_plan.json", previous)
        before = {(m.train_id, m.origin): m for m in previous.movements}
        solutions = []
        for candidate in alternatives.candidates:
            changed = sum(before[(m.train_id, m.origin)] != m for m in candidate.plan.movements)
            payload = _candidate_payload(candidate, args.profiles)
            payload["changed_movements"] = changed
            solutions.append(payload)
            rows.append(
                _summary_row(
                    name,
                    candidate.plan.strategy,
                    candidate.metrics,
                    alternatives.elapsed_ms,
                    changed,
                )
            )
            write_json(folder / f"{candidate.plan.strategy}_plan.json", candidate.plan)
            write_csv(
                folder / f"{candidate.plan.strategy}_track_load.csv", _load_rows(candidate.metrics)
            )
        write_json(
            folder / "resolution.json",
            {
                "incident": scenario.metadata["incident"],
                "violations_before": summarize_violations(violations),
                "elapsed_ms": alternatives.elapsed_ms,
                "budget_exceeded": alternatives.budget_exceeded,
                "diagnostics": alternatives.diagnostics,
                "solutions": solutions,
            },
        )
        if not alternatives.candidates:
            failures.append(name)
        print(
            f"{INCIDENT_LABELS[name]}: нарушений старого плана {len(violations)} → "
            f"{len(solutions)} проверенных решений; {alternatives.elapsed_ms:.0f} мс",
            flush=True,
        )
        for candidate in alternatives.candidates:
            m = candidate.metrics
            print(
                f"  {candidate.plan.strategy}: задержки {m.total_delay_s} с, "
                f"энергия {m.energy_kwh:.1f} кВт·ч, конфликты {m.conflict_count}",
                flush=True,
            )
        for diagnostic in alternatives.diagnostics:
            print(f"  {diagnostic}", flush=True)
    write_csv(output / "comparison.csv", rows)
    write_json(
        output / "summary.json",
        {
            "corridor": base.metadata["corridor"],
            "traffic_source": "synthetic",
            "operational_exactness": False,
            "failed_scenarios": failures,
            "scenarios": ["normal", *args.incidents],
            "results": rows,
        },
    )
    print(f"Результаты: {output.resolve()}")
    return 2 if failures else 0


def main():
    # Russian labels must also work in redirected Windows terminals.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Участник №3: Кокшетау — Астана Нурлы Жол")
    parser.add_argument("--metrics", type=Path, default=Path("config/metrics.json"))
    subs = parser.add_subparsers(dest="command", required=True)
    audit = subs.add_parser(
        "audit", help="Show mapped facts, assumptions and missing operating data"
    )
    audit.add_argument("--infrastructure", type=Path, default=DEFAULT_INFRASTRUCTURE)
    audit.add_argument("--require-exact", action="store_true")
    generate = subs.add_parser("generate", help="Generate the synthetic corridor scenario")
    simulate = subs.add_parser(
        "simulate", help="Inject incidents and recompute plans, speed and load"
    )
    realism = subs.add_parser("realism", help="Run the integrated railsim stochastic comparison")

    def train_count(value):
        count = int(value)
        if not 5 <= count <= 40:
            raise argparse.ArgumentTypeError("Нужно от 5 до 40 поездов")
        return count

    for command in (generate, simulate, realism):
        command.add_argument("--infrastructure", type=Path, default=DEFAULT_INFRASTRUCTURE)
        command.add_argument("--trains", type=train_count, default=8)
        command.add_argument(
            "--interval", type=int, default=900, help="Seconds between departures per direction"
        )
        command.add_argument("--require-exact", action="store_true")
    realism.add_argument(
        "--profile", type=Path, help="JSON containing illustrative or supplied railsim parameters"
    )
    realism.add_argument("--runs", type=int, default=3)
    realism.add_argument("--seed", type=int, default=42)
    realism.add_argument("--plots", action="store_true")
    realism.add_argument("--attribution", action="store_true")
    realism.add_argument("--output", type=Path, default=Path("output/railsim"))
    generate.add_argument(
        "--output", type=Path, default=Path("scenarios/kokshetau_nurly_zhol.json")
    )
    simulate.add_argument("--output", type=Path, default=Path("output/corridor"))
    simulate.add_argument("--budget", type=float, default=5)
    simulate.add_argument(
        "--strategies",
        nargs="+",
        choices=["balanced", "passenger", "eco"],
        default=["balanced", "passenger", "eco"],
    )
    simulate.add_argument("--incidents", nargs="*", choices=INCIDENTS, default=list(INCIDENTS))
    simulate.add_argument(
        "--profiles", action="store_true", help="Include all speed points in JSON"
    )
    solve = subs.add_parser("solve", help="Solve a supplied JSON scenario")
    solve.add_argument("scenario", type=Path)
    solve.add_argument("--previous", type=Path)
    solve.add_argument("--strategy", choices=["balanced", "passenger", "eco"], default="balanced")
    solve.add_argument("--budget", type=float, default=5)
    solve.add_argument("--output", type=Path, default=Path("output/plan_result.json"))
    economics = subs.add_parser(
        "economics", help="Compare saved normal plans using explicit illustrative cost rates"
    )
    economics.add_argument("--simulation", type=Path, default=Path("output/corridor"))
    economics.add_argument("--rates", type=Path, default=Path("config/economics.example.json"))
    economics.add_argument("--output", type=Path, default=Path("output/corridor/economics.json"))
    validate = subs.add_parser("validate", help="Independently validate a plan")
    validate.add_argument("scenario", type=Path)
    validate.add_argument("plan", type=Path)
    validate.add_argument("--previous", type=Path)
    args = parser.parse_args()
    if args.command == "audit":
        report = audit_infrastructure(load_infrastructure(args.infrastructure))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2 if args.require_exact and not report["operational_exactness"] else 0)
    if args.command == "generate":
        scenario = corridor_scenario(
            args.infrastructure,
            train_count=args.trains,
            departure_interval_s=args.interval,
            require_exact=args.require_exact,
        )
        write_json(args.output, scenario)
        print(f"{scenario.metadata['corridor']}: {len(scenario.trains)} поездов; {args.output}")
        return
    if args.command == "simulate":
        raise SystemExit(run_simulation(args, MetricConfig.load(args.metrics)))
    if args.command == "realism":
        scenario = corridor_scenario(
            args.infrastructure,
            train_count=args.trains,
            departure_interval_s=args.interval,
            require_exact=args.require_exact,
        )
        report = run_realism(
            scenario,
            args.output,
            profile_path=args.profile,
            runs=args.runs,
            seed=args.seed,
            plots=args.plots,
            attribution=args.attribution,
        )
        print(
            f"{report['trains_per_run']} поездов в каждом прогоне; источник параметров: {report['calibration_status']}"
        )
        print(f"Отчёт и ограничения для планировщика: {args.output.resolve()}")
        return
    if args.command == "economics":
        scenario = Scenario.model_validate_json(
            (args.simulation / "normal/scenario.json").read_text(encoding="utf-8")
        )
        initial = json.loads((args.simulation / "normal/result.json").read_text(encoding="utf-8"))
        baseline = json.loads((args.simulation / "fcfs.json").read_text(encoding="utf-8"))
        if baseline.get("plan") is None:
            parser.error("Для сравнения нужен допустимый исходный FCFS-план")
        rates = EconomicsRates.model_validate_json(args.rates.read_text(encoding="utf-8"))
        report = compare_plan_costs(
            scenario,
            [Plan.model_validate(c["plan"]) for c in initial["candidates"]],
            Plan.model_validate(baseline["plan"]),
            rates,
            MetricConfig.load(args.metrics),
        )
        write_json(args.output, report)
        write_csv(
            args.output.with_name(args.output.stem + "_comparison.csv"),
            [
                {
                    "kind": report.kind,
                    "traffic_source": report.traffic_source,
                    "rates_status": rates.input_status,
                    **row.model_dump(mode="json", exclude={"reasons"}),
                    "reasons": "; ".join(row.reasons),
                }
                for row in report.rows
            ],
        )
        print(f"Источник ставок: {rates.input_status}. {rates.assumptions_note}")
        for row in report.rows:
            cost = (
                f"{row.generalized_cost_tenge:,.2f} тг"
                if row.generalized_cost_tenge is not None
                else "не вычисляется"
            )
            chosen = " — выбран" if row.plan_id == report.selected_plan_id else ""
            print(f"{row.strategy}: условная стоимость {cost}; допущен={row.eligible}{chosen}")
            for reason in row.reasons:
                print(f"  {reason}")
        print(f"Расчёт и допущения: {args.output.resolve()}")
        return
    scenario = Scenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
    previous = (
        Plan.model_validate_json(args.previous.read_text(encoding="utf-8"))
        if args.previous
        else None
    )
    if args.command == "solve":
        result = solve_plan(
            scenario, strategy=args.strategy, previous=previous, time_budget_s=args.budget
        )
        write_json(args.output, result)
        print(f"{result.status}: {result.elapsed_ms:.0f} мс; {args.output}")
        if result.plan:
            write_json(args.output.with_name(args.output.stem + "_plan.json"), result.plan)
        raise SystemExit(0 if result.plan else 2)
    plan = Plan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    violations = validate_plan(scenario, plan, previous)
    print(json.dumps([v.model_dump() for v in violations], ensure_ascii=False, indent=2))
    raise SystemExit(1 if violations else 0)


if __name__ == "__main__":
    main()
