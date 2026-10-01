# Участник №3: Кокшетау — Астана Нурлы Жол

Python-пакет для **планирования, рекомендаций скорости и прогнозных метрик**
на участке Кокшетау-1 ↔ Астана Нурлы Жол через Астана-1. Здесь находятся
алгоритмы, исходные данные, сценарии сбоев и проверки результатов.

## Что взято с карты и что принято для симуляции

Геометрия получена из OpenStreetMap — источника инфраструктуры
[RailsMaps](https://railsmaps.com/kazakhstan). В сохранённой модели 13 станций
и 12 перегонов; длина вдоль геометрии — **311,821 км**. Это расчётная длина карты,
официальный железнодорожный пикетаж не установлен.

- На первых десяти перегонах поперечные срезы OSM показывают два главных пути.
  Непрерывность и эксплуатационные разрешения требуют отдельной проверки.
- На двух последних перегонах количество путей именно этого маршрута
  не подтверждено. Рядом с Астаной видны и четыре линии, но они могут обслуживать
  разные направления. В расчёте для этих перегонов явно приняты два пути.
- На каждой станции `SIM-1` и `SIM-2`, по 1500 м, — ресурсы симуляции.
  **Фактическое число доступных станционных путей неизвестно.**
- Поезда `SYN-*`, интервалы отправления и нагрузки синтетические, как согласовано
  с пользователем. Пассажирские идут до Нурлы Жол, грузовые — до Астаны-1 и обратно.
- Скорости 80 км/ч для пассажирских и 60 км/ч для грузовых — параметры модели.
  Необработанные теги скорости OSM не принимаются за разрешённые скорости.

Подробные наблюдения, ссылки на объекты карты и допущения сохранены в
[infrastructure.json](data/corridor/infrastructure.json). Команда `audit` перечисляет
пробелы данных; `--require-exact` отклоняет запуск до подтверждения эксплуатационной
схемы. Точная реальная нагрузка по одной карте не определяется.

## Запуск

Из корня проекта:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,realism]'

# Проверить происхождение данных и допущения
python -m app.cli audit

# Создать JSON: восемь синтетических поездов в обоих направлениях
python -m app.cli generate

# Первый опыт: закрыть один главный путь и пересчитать движение
python -m app.cli simulate --incidents main_track_closure --budget 5

# Все 19 сценариев сбоев и три стратегии
python -m app.cli simulate

# Событийная модель railsim: одинаковые события, восемь поездов, три прогона
python -m app.cli realism --trains 8 --runs 3 --plots

# Проверить алгоритмы
python -m pytest -q
```

Для повторения зафиксированных зависимостей доступны `requirements-lock.txt`
и установка `python -m pip install -r requirements-lock.txt`.

`--trains 12 --interval 1200` меняет число поездов и интервал отправления
в каждом направлении. Поддерживаются **5–40 поездов**, интервал от 60 секунд.
`--budget` задаёт мягкий бюджет одного расчёта альтернатив; результат содержит
фактическое время и `budget_exceeded`. Увеличение нагрузки может потребовать
увеличить бюджет.

По умолчанию JSON содержит краткие сведения о профилях. Для всех точек скорости:

```bash
python -m app.cli simulate --incidents speed_restriction --profiles
```

### Где смотреть результат

Папка [output/corridor](output/corridor):

| Файл | Содержимое |
|---|---|
| `infrastructure_audit.json` | Что известно о путях и каких данных не хватает |
| `comparison.csv` | Задержки, энергия, индекс, нарушения и время расчёта |
| `normal/result.json` | Исходные планы и их метрики |
| `normal/active_plan.json` | План до сбоя |
| `<сбой>/resolution.json` | Нарушения прежнего плана, решения и диагностика |
| `<сбой>/<стратегия>_plan.json` | Новый проверенный план |
| `<сбой>/<стратегия>_track_load.csv` | Занятость каждого главного и станционного пути |

Каждый сбой рассматривается отдельно относительно одного исходного плана.
`ten_incidents` вводит десять ограничений одновременно.
В `single_track_operation` разрешение обоих направлений сохраняется в модельном
состоянии до конца горизонта. Ограничение скорости также задаётся для сценария;
автоматическое восстановление скорости по таймеру не моделируется.

Загрузка измеряется в одинаковом окне **0–43 200 секунд** и включает буферы
освобождения ресурсов. Она характеризует резервирования синтетического графика.

### Отдельный расчёт и проверка

```bash
python -m app.cli solve scenarios/kokshetau_nurly_zhol.json --budget 5
python -m app.cli validate scenarios/kokshetau_nurly_zhol.json output/plan_result_plan.json
```

`solve` записывает статус в `output/plan_result.json`, а найденный план — в
`output/plan_result_plan.json`. При перепланировании обеим командам нужен
`--previous <путь_к_предыдущему_плану.json>`.

## Алгоритмы и вход в пакет

Стек: **Python, OR-Tools CP-SAT, NumPy, Pydantic**.

| Код | Назначение |
|---|---|
| [scenarios.py](backend/app/scenarios.py) | Инфраструктура, синтетическая нагрузка, сбои |
| [planning/solver.py](backend/app/planning/solver.py) | Выбор времени и путей с ограничениями |
| [planning/service.py](backend/app/planning/service.py) | Альтернативы в общем бюджете |
| [planning/baseline.py](backend/app/planning/baseline.py) | FCFS для сравнения |
| [advisory/speed.py](backend/app/advisory/speed.py) | Физически достижимая скорость и энергия |
| [validation/plan.py](backend/app/validation/plan.py) | Независимая проверка плана |
| [metrics/quality.py](backend/app/metrics/quality.py) | Задержки и индекс качества |
| [metrics/load.py](backend/app/metrics/load.py) | Загрузка отдельных путей и направления проходов |
| [schemas.py](backend/app/schemas.py) | Проверяемые входные и выходные модели |

Пример использования из другого Python-модуля:

```python
from app.metrics.quality import MetricConfig
from app.planning.service import plan_alternatives
from app.scenarios import corridor_scenario

scenario = corridor_scenario(train_count=8, departure_interval_s=900)
result = plan_alternatives(
    scenario,
    MetricConfig.load("config/metrics.json"),
    time_budget_s=5,
    strategies=("balanced", "passenger", "eco"),
)

print(result.elapsed_ms, result.budget_exceeded, result.diagnostics)
for candidate in result.candidates:
    print(candidate.plan.strategy, candidate.metrics.quality_index)
    # candidate.plan — времена и выбранные пути
    # candidate.profiles — профили по ключу «поезд:перегон»
    # candidate.metrics.track_load — загрузка каждого пути
```

При пересчёте передаются актуальный `Scenario`, `previous=старый_план` и `now_s`
в сценарии. Расчёт сохраняет начатое движение. `UNKNOWN` означает, что решение
не найдено в бюджете; `INFEASIBLE` — несовместимость ограничений модели.

## Выбор варианта по стоимости

После симуляции можно сравнить исходные варианты с FCFS:

```bash
python -m app.cli economics --rates config/economics.example.json
```

Расчёт отдельно показывает стоимость энергии и условные денежные оценки
опозданий. Ставки в примере **учебные**: 50 тг/кВт·ч и 30 000 / 10 000 тг
за час опоздания пассажирского / грузового поезда. Это не тарифы КТЖ.
По умолчанию вариант исключается, если он увеличивает положительное опоздание
хотя бы одного пассажирского поезда относительно проверенного FCFS-плана.

Результат: [economics.json](output/corridor/economics.json) и
[economics_comparison.csv](output/corridor/economics_comparison.csv).
Формулы, существующие решения SBB/Wabtec/DB и границы денежных оценок описаны
в [economics.md](docs/economics.md). Код:
[metrics/economics.py](backend/app/metrics/economics.py).

## Интеграция railsim

Все 16 исходных файлов сохранены без изменений в [vendor/railsim](vendor/railsim).
Адаптер [app/realism](backend/app/realism) подключает исходные модели к нашему
коридору. Параметры задаются в [railsim.example.json](config/railsim.example.json):
исходный `config.py` был незаполненным шаблоном, поэтому новые числа явно помечены
как учебные.

К семи прежним сбоям добавлены дефект рельса, хищение деталей, паводок, снег,
песок, жара, отказ локомотива, отцепка вагона, происшествия на переезде и с людьми,
ремонтное окно и сход. В планировщике учитываются временные ограничения скорости,
удержание занятого пути при отказе и освобождение соседних путей при восстановлении.
Энергия использует сопротивление Davis и заданный уклон; профиль движения отделяет
ожидание помощи от ходового времени.

```bash
python -m app.cli simulate --trains 5 --incidents railsim_snow railsim_loco_failure
python -m app.cli realism --runs 3 --seed 42 --plots --attribution
# Передать ограничения событийной модели нашему CP-SAT планировщику
python -m app.cli solve output/railsim/auto_constraints.json --output output/railsim/planner_result.json
```

[Описание интеграции и всех файлов](docs/railsim-integration.md),
[событийный отчёт](output/railsim/report.json),
[график движения](output/railsim/string_diagram.png).

В исходном `railsim` станции имеют неограниченную вместимость, а подсказка
скорости упрощённо учитывает предотвращённые остановки. Эти ограничения указаны
в отчёте. Наш CP-SAT продолжает проверять конечное число станционных путей.

## Границы модели

Каждый поезд останавливается на каждой моделируемой станции. Один главный путь
резервируется на весь перегон: блок-участки внутри него пока отсутствуют.
Направления путей заданы явно; закрытие пути не разрешает встречное движение
по соседнему. Уклон поддерживается в энергии, но по умолчанию равен нулю:
реальный профиль высот неизвестен. Мощностные характеристики локомотива и
рекуперация требуют дополнительных данных. Рекомендации скорости рассчитаны для этой модели.

## Материалы участника №3

- [Точные задания и что нужно проверить перед защитой](docs/participant-3.md)
- [Математика, сбои, энергия и метрики](docs/algorithms.md)
- [Недорогое решение и расчёт условного эффекта в тенге](docs/economics.md)
- [Результаты проверок и замеров](docs/verification.md)

Геоданные: © OpenStreetMap contributors,
[ODbL](https://www.openstreetmap.org/copyright). Источники и снимок данных:
[sources.json](data/corridor/sources.json), геометрия:
[geometry.geojson](data/corridor/geometry.geojson). Описание источника карты:
[RailsMaps About](https://railsmaps.com/about).
