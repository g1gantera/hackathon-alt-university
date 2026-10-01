# RailFlow — integrated dispatcher

`M_part` combines the React dispatcher from M_part with the planning, physics,
validation, multi-track corridor and railsim tools from `logic`.

## Run locally (Windows PowerShell)

From the project root, using Python 3.12 and Node 22+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt -e .
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Use pnpm 11.25.0, as pinned in frontend/package.json. Open
[the dashboard](http://127.0.0.1:8000) or [API documentation](http://127.0.0.1:8000/docs).
Use one Uvicorn worker. Stop an older server on port 8000 before restarting it.
For Linux/macOS, use `.venv/bin/python` instead of the Windows executable.
The editable install registers the bundled railsim package; do not skip `-e .`.

Docker is also configured: `docker compose up --build`. The web port is bound
to localhost and SQLite uses a named volume. Docker was not available for local
verification. For frontend development, `pnpm --dir frontend dev` proxies to 8000.

## Use the dashboard

1. The default engine is **logic**: 13 stations, 12 sections and eight synthetic
   trains between Kokshetau-1 and Astana Nurly Zhol, via Astana-1. Freight routes
   end at Astana-1. The full Kazakhstan railway layer remains visible.
2. Click **Запустить**. Trains depart after their initial station dwell, following
   the checked FCFS baseline. Use the speed selector to accelerate time. Select
   a train to inspect its position, route, ETA and physically calculated profile.
3. Click **Рассчитать варианты** to request balanced, passenger-priority and eco
   schedules. Candidates are independently checked. A five-second *soft* budget
   may yield fewer than three options; actual elapsed time is shown. An empty
   result or failure does not replace the active plan.
4. Preview a plan on the timetable, then click **Применить**. The server validates
   native track reservations and constraints again. Started movements cannot change.
5. **Добавить сбой** supports train delay, section closure, signal failure and a
   temporary 50% entry-speed restriction. Section closure starts after already
   entered trains clear it; a signal failure prohibits future entries.
6. The **Сценарии logic** selector exposes all 19 predefined stress scenarios:
   individual main/station track closure, single-track operation, ten simultaneous
   restrictions, weather, rail defects, locomotive/wagon failures and others.
   Loading one starts a new history epoch, advances to its detection time, and
   requests plans. The UI asks before replacing the current run.
7. **История** is a read-only view of the last 15 simulated minutes. **Экспорт CSV**
   downloads current factual train state. Admin settings change metric weights.
   **Пути и метрики (JSON)** exposes the native scenario, active reservations,
   forecast metrics, track load and data-quality assumptions.

During calculation or an unresolved incident, the logic engine **freezes model
time**, including trains already moving. WebSocket updates and the interface
remain active. Once a valid plan is applied, a running simulation resumes; a
paused simulation stays paused. This prevents unapproved future departures from
becoming committed while the operator is deciding. Reset restores the baseline.

Live energy within a section is time-interpolated from the native profile's
whole-section energy. Whole-plan energy and forecasts come from logic; the live
estimate is not a measured traction-power integral. The live index uses dashboard
weights; detailed native forecast components are provided separately. Stations,
track permissions and train traffic remain simulation assumptions, not verified
operational railway data. See [logic model documentation](docs/logic-guide.md).

## Original simulator and access

The original six-station, 302.5 km single-track demo, including its original
planner, profiles, metrics and continuous stepping, remains available:

```powershell
$env:DISPATCH_ENGINE = "demo"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Set `DISPATCH_ENGINE=logic` and restart to return to the integrated engine.
These are separate simulation modes; neither silently substitutes for the other.
[Original demo and parser instructions](docs/demo-guide.md) describe that mode.

Copy `.env.example` to `.env` for configuration. Uvicorn needs `--env-file .env`;
Compose reads it automatically. `DEMO_MODE=true` grants local demo admin access.
To enforce roles set it to false and set VIEWER_PASSWORD, DISPATCHER_PASSWORD,
ADMIN_PASSWORD. Viewer reads/exports, dispatcher operates, admin changes weights.
SQLite retains snapshots/events/plans for 48 hours by default (24–72 configurable).
Secrets, virtual environments, raw Overpass downloads and runtime databases are ignored.

## Logic tools retained

The complete CLI and original recorded experiments remain available. These commands
use the same native planner as the dashboard; stochastic batch analysis and economic
comparisons run through the CLI rather than automatically changing live simulation.

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli audit
.\.venv\Scripts\python.exe -m backend.app.cli simulate --incidents main_track_closure --budget 5
.\.venv\Scripts\python.exe -m backend.app.cli simulate
.\.venv\Scripts\python.exe -m backend.app.cli economics
.\.venv\Scripts\python.exe -m backend.app.cli realism --trains 8 --runs 3 --plots
```

Run `simulate` before `economics` to regenerate comparisons. CLI options include
5–40 trains, departure intervals, seeds, profiles, independent validation and
custom scenarios. `python -m app.cli` is retained as a compatibility command.
The original logic engine API is now under `backend.app.*`.
[Detailed instructions](docs/logic-guide.md), [economics](docs/economics.md),
and [railsim integration](docs/railsim-integration.md) cover these features.

## Code and verification

- `frontend/`: existing dashboard, now driven by variable station/train routes.
- `backend/app/integration.py`: native scenario/plan to dashboard adapter and clock.
- `backend/app/planning/`, `advisory/`, `metrics/`, `validation/`, `realism/`: logic algorithms.
- `backend/app/demo_*.py`, `simulator.py`, `domain.py`: preserved original demo.
- `backend/app/main.py`: shared authentication, API, WebSocket, history and CSV.
- `data/corridor/`, `config/`, `vendor/railsim/`: logic geometry, parameters and source.
- `parser/`, `railway_parser.py`: Kazakhstan importer, excluding station features.
- `output/`: original branch's recorded experiments; new generated output is ignored.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests tests -q
pnpm --dir frontend build
```

GitHub Actions runs tests and the frontend build for M_part, logic, main and PRs.
OSM data attribution and ODbL terms are in [data/README.md](data/README.md).
The original imported source/license notes are preserved; no new source license
has been assigned by this integration.

Local integration verification: 182 Python tests passed; production frontend
build passed. The live WebSocket smoke test produced three plans, applied one,
and checked CSV/history. Its planning cycle took 6.36 seconds (above the soft
five-second target); see [recorded result](docs/logic-smoke-result.json).
