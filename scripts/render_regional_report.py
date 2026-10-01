"""Archive a validation matrix and render a standalone, filterable HTML report."""

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    rows = data["cases"]
    keys = {(r["corridor"], r["case"], r["multiplier"]) for r in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate validation cases")
    counts = Counter(r["status"] for r in rows)
    maximum = max(r.get("elapsed_s", 0) for r in rows)
    retries = sum("initial_attempt" in r for r in rows)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    columns = [
        "corridor",
        "case",
        "multiplier",
        "trains",
        "status",
        "solver_status",
        "elapsed_s",
        "within_5s",
        "delay_s",
        "energy_kwh",
        "completed_in_day",
        "completion_s",
        "retry_budget_s",
        "initial_attempt",
        "diagnostics",
        "violations",
    ]
    with (args.output / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )
    visible = columns[:8]
    headings = [
        "Сеть / коридор",
        "Сценарий",
        "Нагрузка ×",
        "Поездов",
        "Итог",
        "Оптимизатор",
        "Время, с",
        "До 5 с",
    ]
    body = []
    for row in sorted(rows, key=lambda r: (r["corridor"], r["multiplier"], r["case"])):
        cells = "".join(f"<td>{html.escape(str(row.get(k, '—')))}</td>" for k in visible)
        detail = html.escape(json.dumps(row, ensure_ascii=False, indent=2))
        body.append(
            f"<tr>{cells}<td><details><summary>Данные</summary><pre>{detail}</pre></details></td></tr>"
        )
    page = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка общей сети Акмолы</title>
<style>body{font:15px system-ui;margin:24px;color:#203942;background:#f5f8f8}
h1{font-size:26px}p,li{max-width:1000px;line-height:1.6}input{padding:10px;width:min(90%,500px)}
.table{overflow:auto;margin-top:20px}table{border-collapse:collapse;background:white;width:100%}
th,td{padding:9px;text-align:left;border-bottom:1px solid #dce4e4;vertical-align:top}
th{background:#dcebea;white-space:nowrap}pre{white-space:pre-wrap;min-width:320px;max-width:600px}
summary{cursor:pointer}a{color:#176d60}</style>
<h1>Проверка общей сети Акмолы · 2 октября 2026</h1>
"""
    page += f"<p><strong>{len(rows)} случаев: {counts['validated']} с допустимым планом, {counts['not_applicable']} неприменимых.</strong> "
    page += (
        f"Сохранено {retries} повторных попыток. Максимальное итоговое время: {maximum:.3f} с.</p>"
    )
    page += """<ul>
<li>8 конфигураций × 3 нагрузки × (19 сбоев + обычный день). Общая сеть: 28 / 56 / 84 поезда.
Это не отдельная проверка каждой из 9702 пар станций.</li>
<li>Начальный бюджет оптимизатора — 2 секунды, повторный — 10. Время включает расчёт и метрики;
часть прогонов выполнялась одновременно с другими тестами. Гарантия 5 секунд не достигнута.</li>
<li>validated означает прохождение независимого валидатора модельных ограничений.
FEASIBLE не означает доказанного оптимума; при нехватке времени возможен проверенный восстановленный план.</li>
<li>not_applicable не считается успешным тестом: нет двух главных путей либо десяти различных ресурсов.</li>
<li>Скоростное ограничение действует на новые входы. Объявленное закрытие занятого пути вводится после его освобождения.
Аварийное торможение и эвакуация при повреждении под движущимся поездом не моделируются.</li>
<li>Инфраструктура основана на исходном графе OSM; граница области архивная (2017).
Номера/ёмкости станционных путей и блоки условные. Пассажирские пары — из справочника КТЖ за май 2026;
отправления, стоянки и грузовой поток синтетические.</li>
<li>Тесты не подтверждают реальные эксплуатационные параметры или плавность интерфейса в браузере.</li>
</ul><p><a href="results.json">Полные результаты JSON</a> · <a href="results.csv">Таблица CSV</a></p>
<label>Фильтр по направлению, сценарию или статусу<br><input id="filter" placeholder="Например: akmola_network"></label>
<div class="table"><table><thead><tr>"""
    page += "".join(f"<th>{h}</th>" for h in headings) + "<th>Подробности</th></tr></thead><tbody>"
    page += (
        "".join(body)
        + """</tbody></table></div>
<script>document.querySelector('#filter').addEventListener('input',event=>{
const query=event.target.value.toLocaleLowerCase();
document.querySelectorAll('tbody tr').forEach(row=>{row.hidden=!row.textContent.toLocaleLowerCase().includes(query)});
});</script></html>"""
    )
    (args.output / "index.html").write_text(page)
    print(
        json.dumps(
            {
                "cases": len(rows),
                "statuses": counts,
                "retry_rows": retries,
                "max_elapsed_s": maximum,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
