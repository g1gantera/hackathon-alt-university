# Run and verify RailFlow

Run commands from the repository root, using a checkout containing the current
frontend, backend, `scenarios/` and `data/kazakhstan_railways.geojson`.
The bundled data is sufficient; downloading OSM again is optional.

## Addresses

| Startup mode | Website | Swagger | Other pages |
| --- | --- | --- | --- |
| Current local demo, port 8001 | http://127.0.0.1:8001/ | http://127.0.0.1:8001/docs | `/stage3.html`, `/redoc`, `/openapi.json` |
| Docker/default README, port 8000 | http://127.0.0.1:8000/ | http://127.0.0.1:8000/docs | Same paths |
| Vite development, port 5173 | http://127.0.0.1:5173/ | Backend port 8000 `/docs` | Proxies `/api` and `/ws` to 8000 |

Use the port printed by the server. Open the HTTP address: opening a standalone
HTML file does not start the API, simulation or WebSocket stream.

## Local installation on Windows

Prerequisites: Python 3.12, Node 22.18+ or Node 24, and pnpm 11.25.0.
The local verification environment used Python 3.12.14 and Node 24.19.0.
The pnpm version is pinned in [package.json](../frontend/package.json).

```powershell
python --version
node --version
npm install --global pnpm@11.25.0
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
.\.venv\Scripts\python.exe scripts/run_local.py
```

Keep the final command running. It starts dispatch on **8001** and three separate
ingestion services on **8101–8103**. Ctrl+C stops its children. For later starts,
only the last command is necessary. Use `--api-port 8000` for the Vite proxy or
`--service-base 8111` if ingestion ports are occupied. Use **one application worker**.
Bare `python -m uvicorn backend.app.main:app --port 8001` remains available for
embedded normalization, but does not start the independent ingestion services.

On Linux/macOS, create the environment with `python3.12 -m venv .venv` and replace
`.\.venv\Scripts\python.exe` with `.venv/bin/python`. Other commands are the same.

Build the frontend **before starting the backend**: static routes are registered
at startup only when `frontend/dist` exists. After frontend edits, rebuild and
refresh with **Ctrl+F5**. After backend edits, stop with **Ctrl+C** and restart.
A restart creates a fresh paused run, preserves saved history, and ends login sessions.

For frontend hot reload, start the API on 8000, then in a second terminal:

```powershell
pnpm --dir frontend dev
```

The browser uses the Vite address; the proxy keeps requests and WebSocket traffic
on that browser origin. Adjust both proxy targets in
[vite.config.ts](../frontend/vite.config.ts) if you change the development API port.

## Docker Compose

With Docker and Compose installed:

```powershell
$env:INGESTION_TOKEN=(.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose config --quiet
docker compose up --build
```

Open port **8000**. The multi-stage image builds the frontend, installs the Python
lockfile and serves both from FastAPI. Compose binds to host loopback and saves
history in `dispatch-data`. It also starts movement, infrastructure and timetable
ingestion on private container ports. The random service token is shared only by
these services; it is separate from user passwords. Stop with **Ctrl+C** or `docker compose down`; ordinary
shutdown retains the named volume. Removing that volume erases its saved history.
Docker was not available in the local audit environment, so container startup is
pending verification on a Docker host.

## Environment and roles

Defaults run the local admin demo without a login. To enable role login, copy
`.env.example` to `.env` **only if `.env` does not already exist**, set
`DEMO_MODE=false` and assign the three role passwords. Do not overwrite an existing
configuration. Load it explicitly for local Python startup:

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --env-file .env
```

Compose reads `.env` automatically. Local startup loads it only with the flag
above; existing environment variables take precedence. The real file is ignored
by Git; `.env.example` contains no passwords. The supervisor generates a fresh
service token if `INGESTION_TOKEN` is empty; Compose requires a nonempty value.

| Variable | Default | Effect |
| --- | --- | --- |
| `DEMO_MODE` | `true` | Automatic local admin role; `false` enables login checks |
| `VIEWER_PASSWORD` | empty | Read-only dashboard/history/export login |
| `DISPATCHER_PASSWORD` | empty | Live simulation, incidents and plan control |
| `ADMIN_PASSWORD` | empty | Dispatcher permissions plus quality/optimization settings |
| `RETENTION_HOURS` | `48` | Real-time history retention, clamped to 24–72 hours |
| `DATABASE_URL` | Local `data/dispatch.sqlite` | SQLite connection URL; parent directory must exist |
| `COOKIE_SECURE` | `false` | Set `true` when serving via HTTPS |
| `INGESTION_MODE` | `embedded` for bare Uvicorn | Supervisor and Compose force `remote` |
| `INGESTION_TOKEN` | empty | Shared bearer token; supervisor generates one when empty |
| `MOVEMENT_INGESTION_URL`, `INFRASTRUCTURE_INGESTION_URL`, `TIMETABLE_INGESTION_URL` | Local 8101–8103 | Supervisor/Compose supply matching service URLs |

Compose supplies its own `DATABASE_URL=sqlite:////storage/dispatch.sqlite` to use
the volume. Changing the environment requires restarting the backend/container.
Changing quality weights through the UI takes effect immediately for the current
run. Reset restores default scenario settings.

## Short manual walkthrough

1. Open the dashboard, confirm eight trains, six stations and quality 100 in a
   fresh paused run. **Весь Казахстан** shows the nationwide network.
2. Start at **30x**. Select a train and inspect coordinates, route, speed and delay.
3. Reset and leave paused. Close **section-1** for ten minutes: default quality
   changes from 100 to 96 immediately; resolving it before time advances restores 100.
4. Add a ten-minute delay to **№101**. Inspect the applied plan's comparison.
   For manual selection, disable **Автоприменение**, add an incident, then use
   **Диспетчер → Сравнить график → Применить**.
5. Reset. Select **№105** under **Аналитика → Скорость и энергия** and calculate
   an economy proposal. The baseline demonstrates about 52.27 kWh forecast saving.
6. Start at 30x for 10–20 seconds, pause, then open **Ещё → История и отчёт CSV**.
   Replay saved frames, inspect an event, export the selected window, and return
   **В эфир**. Reset once more to confirm the previous run remains in history.

The [feature guides](../README.md#documentation) explain the model assumptions
behind these examples. This walkthrough prepares the final live/recorded demo;
it is not evidence of the browser latency requirement by itself.

## Automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests tests -q
pnpm --dir frontend test
pnpm --dir frontend build
```

Smoke scripts need a running **demo-mode** server and reset that server before
and after each run. Use a separate instance for verification, with its own database:

```powershell
# Separate terminal, from the repository root
New-Item -ItemType Directory -Force .logs | Out-Null
$env:DEMO_MODE='true'
.\.venv\Scripts\python.exe scripts/run_local.py --api-port 8012 --service-base 8111 --database .logs/verification.sqlite
```

Run the following **sequentially** in another terminal, so they do not reset each
other's scenarios:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_replanning.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_replan_comparison.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_speed_advice.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_quality_settings.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_history_reports.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_realtime_timing.py http://127.0.0.1:8012
.\.venv\Scripts\python.exe scripts/smoke_performance.py http://127.0.0.1:8012 --repetitions 3 --output .logs/performance.json
```

Stop the verification server when finished. Those environment overrides belong
to its terminal; use a normal terminal to start the main demo. No smoke script
measures actual browser painting. The opt-in browser probe, measured results and
remaining qualifications are in [final verification](final-verification.md).

For a self-contained four-service incident check (starts and stops its own services):

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --api-port 8013 --service-base 8121 --database .logs/service-check.sqlite --verify
.\.venv\Scripts\python.exe scripts/verify_retention.py
```

`--verify` resets its supplied database's live test scenario and forces demo mode.
Use a disposable database. The retention script uses a controlled clock to test
24/48/72-hour boundaries, restart/export stability, paused cleanup and a dense
2 Hz replay window. It does not wait 72 real hours. Both checks also run in CI.
`GET /api/health` reports ingestion mode, readiness/counters and allocated/reusable
history bytes. Sustained dense writes require several GB of storage; see the report.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Browser cannot connect | Keep Uvicorn running; use its printed port, not a local HTML file |
| API works but dashboard is 404 | Build `frontend/dist`, then restart the API |
| UI shows old controls | Rebuild frontend; hard-refresh the browser |
| Vite shows disconnected state | API must match the proxy port (8000 by default) |
| Login unavailable | Load `.env`, set all required role passwords and restart; inspect `DEMO_MODE` |
| Map background unavailable | External tiles need internet; railway data and the schematic are local |
| Moving after calculation does not resume | A paused model stays paused after plan application; click **Запустить** |
| Model waits above 60x | Apply/retry the pending plan, or lower speed to 60x or less |
| End of route | Completed simulation pauses; **Сброс** starts a new run and keeps history |
| Stale plan rejected (409) | Recalculate using current incidents, time and settings |
| Port already in use | Use another free port and its matching URL/proxy; avoid starting two copies against one live scenario |
| Ingestion degraded or startup fails | Check all three services, matching token/URLs and `/api/health`; the supervisor stops its children when one exits. Restart the supervisor after correcting the failure |
