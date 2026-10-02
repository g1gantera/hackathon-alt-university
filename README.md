# Railflow — railway dispatch hackathon prototype

A working FastAPI + Leaflet railway simulation around the original Kazakhstan OSM map. The map, compact data, full graph and builder are **unchanged byte-for-byte**. Trains, signals, locks, occupied sections and incidents are separate overlays.

**Demonstration and advisory system. Not a replacement for certified railway safety systems.** Do not connect this prototype to railway control equipment.

## Run

The presentation uses the liquid-glass layout from `M_part` commit `63ea044`:
quality index at the upper left, compact train list at the right, Map/Dispatcher/
Analytics/History navigation, and separate simulation and map controls at the bottom.
`static/reference.css` and `static/reference-layout.css` provide its appearance;
`static/reference-ui.js` rearranges existing controls and forwards filter/speed
shortcuts to their existing handlers. It also stores the visual theme preference.
The original backend, application JavaScript, map data and API calls are unchanged.
Reload the page to see it; no frontend build is required.

Python 3.11+ is required; tested with Python 3.14.4. No Node build is needed for the application.

The dashboard uses a fullscreen map with floating liquid-glass panels. Use the
scenario icon at the bottom-left to choose a scenario, and click a train for its
driving advisory. **History** is in the header; the **•••** menu contains configuration,
guides and sign-out. On phones the train list becomes a collapsible bottom sheet.
The moon/sun button selects the visual theme. The interface supports reduced motion,
keyboard navigation, and Kazakh/Russian/English. The original map assets, simulation,
dispatch weights and live/replay data are preserved.

```bash
./run.sh
# Open http://127.0.0.1:8000
```

The script creates `.venv`, installs `requirements.txt` and starts **one** authoritative server worker. Alternatively:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Choose **KZ**, **RU**, or **ENG** on the sign-in screen or in the dashboard header for Kazakh, Russian, or English. The selection updates the dashboard and both map views immediately and is remembered in this browser. Switching languages preserves open forms, filters, and replay state. User-entered names and notes, source place names, API values, and exported journal data retain their original content.

| Role | Username | Default mock password |
|---|---|---|
| Dispatcher | `dispatcher` | `demo-dispatch` |
| Administrator, including settings | `admin` | `demo-admin` |

Override passwords with `RAIL_DISPATCHER_PASSWORD` and `RAIL_ADMIN_PASSWORD`; see [.env.example](.env.example). This example is not automatically loaded. `RAIL_HOST`, `RAIL_PORT`, `RAIL_DATA_DIR`, and `RAIL_SECURE_COOKIE` are supported. Default bind is loopback. A hosted demo should use HTTPS with secure cookies. No external service secrets are required.

## Demonstrate

1. Sign in, select **Opposing trains · passing loop**, and click **Load demo**.
2. Press **Start** at 30× or 120×. `KZ-101` enters existing siding E47083. `KZ-009` uses the existing main-line bypass. Select a train for its explanation and driving advice.
3. Change importance. A new plan considers the preference while retaining already committed authority.
4. In **Incidents**, choose a breakdown, signal failure, closure or restriction. Set a duration or leave it blank for manual clearance. **Inject 10 reproducible delays** demonstrates an atomic batch. Clear incidents to recover.
5. Inspect **Timetable & diagram**, then **History & replay**. Replay is visibly read-only; the live simulation continues independently. Export CSV.
6. Load **Overtaking**, **Importance & fairness**, or **Closure & alternative route** for other cases. Loading a scenario resets the current run and preserves its journal.

See [the exact scenarios](docs/DEMO.md), [architecture and API](docs/ARCHITECTURE.md), [rules and formulas](docs/RULES.md), and [verification](docs/VERIFICATION.md). Open the [12-slide PowerPoint](artifacts/Railflow-Hackathon-final.pptx) or the [browser presentation](http://127.0.0.1:8000/presentation). Measured results and screenshots are in [artifacts](artifacts/). Only the pasted requirements attachment was available; no additional case document was present.

Optional presentation rebuild instructions are in [docs/PRESENTATION.md](docs/PRESENTATION.md).

## Implemented

- Authoritative 0.2 s physics, acceleration/braking, speed limits, scheduled departures/stops/dwells, train length, terminal occupancy and rear clearance.
- Progressive movement authority for following trains, exclusive edges/junctions, directional route commitments, deterministic scoring, FIFO waiting protection, existing-route alternatives, cycle detection and safe holds.
- Importance 1–10 independent of category; editable arrival targets; conservative station capacity of one mapped vertex; existing siding fit validation.
- Functional direction-specific signal overlays and switch locks from the same authority and incident state as movement.
- Five incident types, future start times, manual/duration clearance, affected-train detection, seeded random incidents, atomic batches of ten and recovery explanations.
- Advisory speed profile, simplified traction kWh, rolling quality breakdown, timetables, time–distance diagram, searchable journal and CSV export.
- Full-state SSE at 2 Hz, bounded queues, versions, stale indicators, reconnect/backoff/resync, source sequence validation and idempotent commands.
- Cookie sessions and HTTP Basic, admin-only configuration, SQLite history (24–72 h retention), 15-minute replay, health/metrics and OpenAPI.

## Source structure

| Path | Purpose |
|---|---|
| `map.html`, `map_data.js`, `network.json`, `build_network.py` | Preserved original assets; SHA-256 baseline in `docs/map-preservation.json` |
| `backend/network.py` | Read-only directed routing and existing-loop discovery |
| `backend/models.py` | Validated commands and configuration |
| `backend/engine.py` | Clock, physics, incidents, forecasts and replay samples |
| `backend/dispatch.py` | Preferences, resource locks, rear clearance and waiting protection |
| `backend/ato.py`, `backend/quality.py` | Speed advice, energy and measured quality factors |
| `backend/history.py` | SQLite journal, retention and bounded event bus |
| `backend/app.py` | API, authentication, SSE, deduplication and metrics |
| `static/` | Responsive dashboard and separate Leaflet overlay adapter |
| `tests/`, `scripts/` | Verification scenarios and repeatable measurements |

## Verify

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark.py
.venv/bin/playwright install chromium --only-shell
# With the server running:
.venv/bin/python scripts/browser_check.py
# Alternative with installed Firefox and Node 22+:
node scripts/browser_firefox.mjs
# Glass UI checks: use an isolated server on port 8013 (creates demo state).
# RAIL_DATA_DIR=.build/glass-check-data .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8013
node scripts/glass-browser.mjs
# Localization checks (Node 22+); browser check requires an isolated demo server:
node --test scripts/i18n.test.mjs
RAIL_TEST_URL=http://127.0.0.1:8012 node scripts/i18n-browser.mjs
# Read-only SSE cadence and resynchronization check:
.venv/bin/python scripts/stream_check.py
```

`RAIL_TEST_URL` overrides the browser target. Tests use the supplied graph and temporary/in-memory databases. Browser verification resets its target demo. Measurements are written to `artifacts/backend-results.json` and `artifacts/browser-results.json`.

The delivered run passed **36 tests** and verified desktop/mobile behavior in **Firefox 155.0**. UI updates measured p95 **15 ms** after event receipt; the live SQLite-backed ten-incident batch took **14.08 ms**. See [measurement scope and limitations](docs/VERIFICATION.md) before interpreting these local results.

Swagger: <http://127.0.0.1:8000/docs>. OpenAPI: `/openapi.json`. Health: `/health`. Untouched original map: `/map.html`.

```bash
curl -u dispatcher:demo-dispatch http://127.0.0.1:8000/api/state
curl -u dispatcher:demo-dispatch -H 'Content-Type: application/json' \
  -d '{"scenario":"passing"}' http://127.0.0.1:8000/api/demo
curl -N -u dispatcher:demo-dispatch http://127.0.0.1:8000/api/stream
```

## Limits that matter

The graph contains 841 synthetic gap-healing ways, 31 suspected gaps and unverified station snaps. Visual geometry is retained, but synthetic links and urban rail categories are excluded from dispatching. Connections use existing vertices and turns use a ≤90° heading rule; no verified interlocking table was supplied.

The dispatcher now uses **logical automatic blocks and rolling reservations**, informed by [Kazakhstan's published operating and signalling rules](docs/KAZAKHSTAN_RULES.md). Long mapped edges are subdivided into assumed signal blocks (default maximum 2 km); reservations cover a local lookahead (default 5 km, enlarged for braking and rounded to a block boundary). Following trains can occupy different blocks of one original edge, and a new train 500 km ahead is not blocked by a whole-trip exclusive reservation. Occupied and reserved blocks are shown separately. Settings are simulation assumptions, not surveyed KTZ signal locations or mandated distances.

**This is a partial adaptation, not an exact KTZ dispatcher.** Opposing/crossing route commitments still conservatively extend to the next scheduled stop because actual interstation sections, receiving tracks and station interlocking plans are missing. Remote opposing waits can therefore remain. No extra track, platforms or connectivity are invented. Station capacity and usable lengths are not surveyed. Arriving trains retain their footprint until simulated terminal withdrawal; origins use boundary admission. Dispatch scores are a heuristic, not a verified national priority formula.

The deterministic heuristic is not globally optimal. It reports wait-for cycles and holds safely; it does not reverse, shunt, break locks or guarantee recovery from every multi-stop deadlock. Fairness applies when a feasible route becomes available. A late incident cannot stop a train instantaneously: its reserved braking envelope remains protected, and the train may overrun a newly imposed closure while braking. Signals are functional overlays, not surveyed equipment.

ETAs and alternatives are estimates, not full stochastic rollouts. Quality forecasts show only the **schedule contribution** with other factors fixed, not fabricated measured improvement. The distance diagram uses each train’s distance from its own origin rather than absolute corridor chainage. Replay samples about once per simulation second. History/settings survive restart, but live motion does not: a restart begins a new paused run. Random incident types/times/targets/durations are seeded; user incident IDs are UUIDs.

Leaflet, application fonts and railway geometry work locally. Basemap tiles need internet; use **None – tracks only** in Layers offline or if the provider blocks access. OSM attribution is retained. Retention is a wall-clock policy, not a disk quota; usage depends on simulation speed and train count.
