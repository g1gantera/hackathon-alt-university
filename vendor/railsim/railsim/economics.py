"""Сводная таблица сценариев и экономический эффект автодиспетчера."""
import math

import pandas as pd

KPI_LABELS = {
    "trains_total": "Поездов всего",
    "trains_completed": "Доехали до конца линии",
    "avg_line_delay_h": "Средняя задержка на линии, ч",
    "p90_line_delay_h": "Задержка 90-й перцентиль, ч",
    "total_line_delay_h": "Суммарная задержка, поездо-ч",
    "unplanned_stops": "Остановки «на красный»",
    "smoothed_stops": "Остановок избежали (подсказка скорости)",
    "electric_kwh": "Электроэнергия, кВт·ч",
    "diesel_l": "Дизельное топливо, л",
    "derailments": "Сходы с рельсов",
    "loco_failures": "Отказы локомотивов",
    "wagon_setouts": "Отцепки неисправных вагонов",
    "crossing_accidents": "ДТП на переездах",
    "people_incidents": "Происшествия с людьми",
    "hazards_total": "Угроз на пути всего",
    "hazards_undetected": "Угроз не обнаружено сразу",
    "closure_hours": "Перерывы движения, перегоно-ч",
    "avg_border_h": "Время на погранпереходе, ч/поезд",
    "avg_slot_hold_h": "Придержка под слот парома, ч/поезд",
    "avg_port_yard_queue_h": "Ожидание места в порту, ч/поезд",
    "avg_port_wait_h": "Ожидание парома, ч/поезд",
    "delay_cost_kzt": "Стоимость задержек на линии, тг",
    "border_cost_kzt": "Стоимость времени на границе, тг",
    "port_cost_kzt": "Стоимость ожидания в порту, тг",
    "energy_cost_kzt": "Затраты на энергию, тг",
    "incident_cost_kzt": "Ущерб от происшествий, тг",
    "total_cost_kzt": "ИТОГО затраты, тг",
}


def summary_table(df: pd.DataFrame) -> pd.DataFrame:
    """Средние по прогонам: строки — показатели, столбцы — сценарии."""
    cols = [c for c in KPI_LABELS if c in df.columns]
    order = list(dict.fromkeys(df["scenario"]))
    mean = df.groupby("scenario")[cols].mean().loc[order].T
    if "baseline" in mean.columns and "auto" in mean.columns:
        base = mean["baseline"]
        mean["auto vs baseline, %"] = [
            (a - b) / b * 100 if b else float("nan") for a, b in zip(mean["auto"], base)]
    mean.index = [KPI_LABELS[c] for c in mean.index]
    return mean


def economic_effect(df: pd.DataFrame, cfg) -> dict:
    mean = df.groupby("scenario")["total_cost_kzt"].mean()
    if "baseline" not in mean or "auto" not in mean:
        return {}
    annual = 365.0 / float(cfg.SIM_DAYS)
    savings = (mean["baseline"] - mean["auto"]) * annual
    net = savings - float(cfg.AUTO_SYSTEM_OPEX_KZT_YEAR)
    payback = float(cfg.AUTO_SYSTEM_CAPEX_KZT) / net if net > 0 else math.inf
    revenue = float(cfg.REGULATED_REVENUE_KZT_YEAR)
    return {
        "Экономия в год (до затрат на систему), тг": savings,
        "Чистая экономия в год, тг": net,
        "Срок окупаемости, лет": payback,
        "Экономия в % от регулируемой выручки": savings / revenue * 100 if revenue else float("nan"),
    }


def attribution_table(df: pd.DataFrame) -> pd.DataFrame:
    """Вклад отдельных функций: экономия относительно baseline для сценариев auto:<функция>."""
    mean = df.groupby("scenario")["total_cost_kzt"].mean()
    if "baseline" not in mean:
        return pd.DataFrame()
    rows = []
    for name, val in mean.items():
        if name.startswith("auto:"):
            rows.append({"Функция": name.split(":", 1)[1],
                         "Экономия за период, тг": mean["baseline"] - val})
    return pd.DataFrame(rows).sort_values("Экономия за период, тг", ascending=False) if rows else pd.DataFrame()
