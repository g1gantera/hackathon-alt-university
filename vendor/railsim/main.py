"""
Запуск симуляции.

  python main.py --check                 проверить, что заполнено в config.py
  python main.py                         сравнить baseline и auto
  python main.py --attribution           + вклад каждой функции автодиспетчера отдельно
  python main.py --config my_line.py     другой файл конфигурации (например, для другой линии)
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

from railsim import economics, plots, wagons
from railsim.simulation import run_monte_carlo
from railsim.util import Scenario
from railsim.validate import find_errors, find_missing


def load_config(path):
    spec = importlib.util.spec_from_file_location("user_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt(x):
    if isinstance(x, float):
        return f"{x:,.2f}".replace(",", " ")
    return str(x)


def main():
    ap = argparse.ArgumentParser(description="Симуляция ж/д коридора: baseline vs автодиспетчер")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.py")))
    ap.add_argument("--check", action="store_true", help="только проверить конфигурацию")
    ap.add_argument("--attribution", action="store_true", help="вклад каждой функции отдельно")
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--out", default="output", help="папка для результатов")
    args = ap.parse_args()

    cfg = load_config(args.config)
    missing = find_missing(cfg)
    if missing:
        print(f"Не заполнено параметров: {len(missing)}")
        for m in missing:
            print("  •", m)
        sys.exit(1)
    errors = find_errors(cfg)
    if errors:
        print("Ошибки в конфигурации:")
        for e in errors:
            print("  •", e)
        sys.exit(1)
    if args.check:
        print("Конфигурация заполнена полностью ✔")
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    enabled = {f for f, on in cfg.AUTO_FEATURES.items() if on}
    scenarios = [Scenario("baseline", set()), Scenario("auto", enabled)]
    if args.attribution:
        scenarios += [Scenario(f"auto:{f}", {f}) for f in sorted(enabled)]

    print(f"Симуляция: {cfg.SIM_DAYS} сут, {cfg.N_RUNS} прогонов, сценариев: {len(scenarios)}")
    df, details = run_monte_carlo(cfg, scenarios)
    df.to_csv(out / "runs.csv", index=False)

    main_df = df[df["scenario"].isin(["baseline", "auto"])]
    table = economics.summary_table(main_df)
    table.to_csv(out / "summary.csv")
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 160,
                           "display.float_format", lambda v: fmt(float(v))):
        print("\n=== Показатели (среднее по прогонам за период) ===")
        print(table)

    eff = economics.economic_effect(main_df, cfg)
    if eff:
        print("\n=== Экономический эффект (в пересчёте на год) ===")
        for k, v in eff.items():
            print(f"  {k}: {fmt(v)}")

    if args.attribution:
        att = economics.attribution_table(df)
        att.to_csv(out / "attribution.csv", index=False)
        print("\n=== Вклад функций автодиспетчера (каждая включена отдельно) ===")
        print(att.to_string(index=False, float_format=lambda v: fmt(float(v))))

    if cfg.USE_WAGON_MODULE:
        print("\n=== Распределение порожних вагонов ===")
        for k, v in wagons.empty_wagon_distribution(cfg).items():
            print(f"  {k}: {fmt(v)}")
    if cfg.USE_CIS_EXCHANGE:
        print("\n=== Обмен вагонами между странами СНГ ===")
        for k, v in wagons.cis_exchange(cfg).items():
            print(f"  {k}: {fmt(v)}")

    if not args.no_plots:
        plots.string_diagram({k: v for k, v in details.items() if k in ("baseline", "auto")},
                             cfg, out / "string_diagram.png", getattr(cfg, "PLOT_HOURS", None))
        plots.kpi_bars(main_df, out / "kpi.png")
        print(f"\nГрафики: {out / 'string_diagram.png'}, {out / 'kpi.png'}")
    print(f"Таблицы: {out / 'summary.csv'}, {out / 'runs.csv'}")


if __name__ == "__main__":
    main()
