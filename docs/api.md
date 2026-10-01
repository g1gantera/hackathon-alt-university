# API guide

The browser, REST API and WebSocket stream share one origin. Default Docker port:
8000; current local demo port: 8001. In the links below, change the port to match
your server.

- [Swagger UI](http://127.0.0.1:8001/docs)
- [OpenAPI JSON](http://127.0.0.1:8001/openapi.json)
- [ReDoc](http://127.0.0.1:8001/redoc)
- [Detailed WebSocket contract](realtime.md)

FastAPI generates request schemas and route parameters in OpenAPI. Many response
objects are currently dynamic dictionaries, so their detailed field semantics are
documented here and in [frontend types](../frontend/src/types.ts). WebSocket
messages are documented separately; they are not OpenAPI operations. Swagger UI
and ReDoc load their UI assets from external CDNs; `/openapi.json` remains local.

## Authentication and roles

The ingestion services have their own `/docs` and `/openapi.json` on their local
ports 8101–8103 (private in Compose). `POST /v1/normalize` requires
`Authorization: Bearer <INGESTION_TOKEN>` and a version-1 frame containing
`kind`, `epoch`, `state_version`, `sim_time_s` and `payload`. Movement payloads
contain trains; infrastructure contains sections/switches; timetable contains
the active plan/ID. Invalid values, duplicate identities or the wrong channel
return 422; missing/bad credentials return 401. `GET /health` exposes channel
readiness and accepted count. These are service contracts, not browser control
endpoints; see [schema validation](../backend/ingestion/contracts.py).

`DEMO_MODE=true` grants admin access for the local demo. When false,
`POST /api/auth/login` accepts `role` (`viewer`, `dispatcher`, `admin`) and
`password`, setting the `dispatch_session` cookie. Passwords come from the
matching environment variables. Cookies are HttpOnly, SameSite=strict and expire
after eight hours. Use the same browser origin or an HTTP cookie jar for subsequent
requests. `COOKIE_SECURE=true` requires HTTPS.

| Role | Live application permissions |
| --- | --- |
| Viewer | Read state, plans, forecasts, history and CSV; use isolated stage-3 demos |
| Dispatcher | Viewer permissions plus simulation, incidents, replanning and plan application |
| Admin | Dispatcher permissions plus `PUT /api/settings` |

`GET /api/auth/me` returns `{role, demo}`. `POST /api/auth/logout` clears the
session. Backend restart ends all sessions. The API checks roles even if a request
bypasses disabled UI controls. Swagger/OpenAPI pages themselves are public on the
server's bound address.

## Routes

V = viewer or higher; D = dispatcher or admin; A = admin. In demo mode all are
available. Identifiers come from API responses; examples `T01` and `section-1`
belong to the bundled scenario.

| Method | Route | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/api/health` | Public | Ingestion readiness/counters and history storage statistics; contains no credentials |
| GET | `/api/state` | V | Full live snapshot, dispatch report, quality and realtime identity |
| GET | `/api/trains` | V | Fleet telemetry and sampling metadata |
| GET | `/api/trains/{train_id}` | V | One train and sampling metadata |
| GET | `/api/topology` | V | Stations, sections, geometry and model assumptions |
| GET | `/api/map` | V | Corridor GeoJSON: six station Points and five section LineStrings |
| GET | `/api/network` | V | Full bundled Kazakhstan railway GeoJSON |
| GET | `/api/quality` | V | Actual metrics, valid-plan forecast or null, current-formula trend |
| POST | `/api/simulation/{action}` | D | `start`, `pause`, `reset`, or `speed` |
| POST | `/api/incidents` | D | Add delay, closure or signal incident and queue replanning |
| POST | `/api/incidents/{incident_id}/resolve` | D | Resolve incident early and recalculate |
| GET | `/api/replanning` | V | Options and current job status |
| PUT | `/api/replanning` | D | Replace `auto_apply` and `policy` settings |
| POST | `/api/replanning/retry` | D | Retry under latest conditions and application mode |
| POST | `/api/replan` | D | Calculate candidates for manual review |
| GET | `/api/plans` | V | `{plans, active, baseline}` |
| GET | `/api/plans/{plan_id}/comparison` | V | Evaluate that candidate against the current active timetable |
| GET | `/api/dispatch` | V | Optional `plan_id`; independent conflicts and pass/wait decisions |
| POST | `/api/plans/{plan_id}/apply` | D | Revalidate and install a candidate |
| GET | `/api/trains/{train_id}/profile` | V | Active speed profile, energy and ETA |
| GET | `/api/trains/{train_id}/advice` | V | Live speed advice plus detailed profile |
| POST | `/api/trains/{train_id}/eco-plan` | D | Propose slower movement using available dwell slack; requires pause |
| GET | `/api/settings` | V | Current scenario/scoring settings |
| PUT | `/api/settings` | A | Replace settings, recalculate metrics and invalidate candidate plans |
| GET | `/api/history/runs` | V | Latest 20 retained runs and current epoch |
| GET | `/api/history/window` | V | Manifest for 5/10/15 model minutes, with saved frames and events |
| GET | `/api/history/snapshots/{record_id}` | V | Saved snapshot; `epoch` is required |
| GET | `/api/reports/history.csv` | V | Frozen archive report; requires `epoch`, `to`, `through_id` |
| GET | `/api/history` | V | Legacy current-run snapshots, clipped to last 900 model seconds |
| GET | `/api/report.csv` | V | Compact current fleet CSV, not the full archive report |
| GET | `/api/stage3/scenario` | V | Isolated `opposing`, `following` or `fleet` scenario |
| POST | `/api/stage3/solve` | V | Solve the isolated scenario without changing live dispatch |

## Read live state

`/api/health` returns `status: ready|degraded`, `ingestion` (`mode`, `accepted`,
`failed`, `coalesced`, last successful receipt) and `history` (retention, allocated
and reusable bytes). A 200 response alone does not mean readiness: inspect status.
Remote WebSocket snapshots also carry `ingestion.services` and `latency_ms`.
REST reads reflect the authoritative simulator; WebSocket publication and snapshot
archival wait for a matching normalized batch from all three services.

```powershell
$railBase='http://127.0.0.1:8001'
$railState=Invoke-RestMethod "$railBase/api/state"
$railState.trains | Select-Object id,number,status,speed_mps,delay_s,route_name
$railState.metrics | Select-Object index,assessment,conflicts,quality_version
```

Distances use metres; speeds use m/s; energy uses kWh. `sim_time_s` is model time
starting at zero (displayed as 08:00 in the UI). `realtime.observed_at` is UTC wall
time. Coordinates are WGS84 `[longitude, latitude]`. Train data is explicitly
labelled as simulation. See [architecture identities](architecture.md#state-units-and-clocks)
and [train field definitions](realtime.md#api-contract).

`metrics` includes five component scores, weights, loss contributions, formula
signature and categories. `forecast: false` describes observed movement. The
`/api/quality` forecast is null while held or when the timetable is invalid.
Missing arrival data stays null/neutral rather than claiming measured punctuality.

## Commands and candidate lifecycle

The following examples mutate the demo scenario. A multiplier changes simulation
time, not the train's physical speed limit.

```powershell
Invoke-RestMethod "$railBase/api/simulation/speed" -Method Post -ContentType 'application/json' -Body '{"multiplier":30}'
Invoke-RestMethod "$railBase/api/simulation/start" -Method Post
Invoke-RestMethod "$railBase/api/simulation/pause" -Method Post
```

The generic simulation route parses the speed body at runtime. Supported
`multiplier` values are positive integers through JavaScript's maximum exact
integer, 9,007,199,254,740,991. Above 60x, the clock waits during replanning or an
incident-related hold. A completed scenario stops at final tail release.

Create an incident with:

```json
{"kind":"closure","target_id":"section-1","duration_s":600}
```

`kind` is `closure`, `signal` or `delay`; duration is 30–7200 model seconds. Train
delays target a waiting train (for example `T01`), while closures/signals target a
section. The returned incident has an ID and model start/end times. New departures
are held until a valid plan is installed; already-started movements are preserved.

Set manual review with `PUT /api/replanning`:

```json
{"auto_apply":false,"policy":"balanced"}
```

The other policy is `passenger_priority`. Replanning is asynchronous: inspect
`replan_status.status` in snapshots (`queued`, `calculating`, `review`, `applied`,
`failed`, or `idle`). A successful queue response does not mean a plan was applied.
Read `/api/plans`, compare a candidate ID, then apply it. A calculation result that
has become stale cannot overwrite current state. Application always revalidates.

The comparison response identifies the epoch, constraint version, active and
candidate plan IDs, evaluation time and `applicable` flag. Its `comparison` stores
same-condition before/after forecasts, changed movement times and terminal ETAs.
An advancing clock can produce `applicable: false` even while the candidate still
exists. After application the comparison is captured as `forecast_at_application`.
See [replanning guide](replanning.md).

## Runtime quality configuration

`PUT /api/settings` replaces a settings object; omitted values use model defaults.
Read first and modify the returned object when preserving other settings:

```powershell
$railSettings=Invoke-RestMethod "$railBase/api/settings"
$railSettings.quality_formula='weighted_geometric'
Invoke-RestMethod "$railBase/api/settings" -Method Put -ContentType 'application/json' -Body ($railSettings | ConvertTo-Json -Depth 5)
```

`quality_weights` has `schedule`, `energy`, `capacity`, `conflicts`, and
`arrival_accuracy`. Each weight is finite, between 0 and 1000, with at least one
positive. The backend normalizes their sum. `quality_formula` selects
`weighted_mean` or `weighted_geometric`. Thresholds require
`0 <= quality_threshold_attention < quality_threshold_normal <= 100`.

Normalization, arrival tolerance, conflict penalty and passenger/freight priority
weights are also configurable. Legacy `delay_weight`/`energy_weight` apply only
when explicit `quality_weights` is omitted. Saving clears candidates and publishes
new metrics. Reset restores defaults; archived scores retain their saved formula.
The complete formula is documented in [README Step 6](../README.md#step-6-movement-quality-index).

## History and CSV

Obtain a manifest before requesting a frame or report. `minutes` accepts 5, 10
or 15. Optional `epoch` chooses a saved run; optional `to` chooses an earlier model
time. The returned `through_id` freezes the report boundary.

```powershell
$railWindow=Invoke-RestMethod "$railBase/api/history/window?minutes=15"
$railReport="$railBase/api/reports/history.csv?epoch=$($railWindow.epoch)&minutes=15&to=$($railWindow.to_s)&through_id=$($railWindow.through_id)"
Invoke-WebRequest $railReport -OutFile 'railflow-report.csv'
```

CSV is UTF-8 with BOM. `record_type` distinguishes quality, components, formulas,
trains, timetable violations, events and before/after replanning records. Actual
observations and forecasts have different `basis` values. Delay/energy fields are
cumulative; do not sum repeated snapshots. Archive reads and exports never rewind
the live model. A missing or expired run/frame returns 404.

## Realtime and error handling

Connect to `ws://127.0.0.1:8001/ws` (or `wss` over HTTPS), using the same session
cookie when login is enabled. Same-origin browser connections are checked.
The first message is a full `state.updated` snapshot; subsequent messages include
state, metrics and operational events. Clients track `stream_id`, `seq`, `epoch`
and `state_version`; after a gap, reconnect for a fresh state.

| Response | Meaning / recovery |
| --- | --- |
| 401 | Sign in, or session expired |
| 403 | Role cannot perform the operation |
| 404 | Unknown train/incident/plan/action, or unavailable archived data |
| 409 | Operating conditions prevent the action; pause, refresh or recalculate as indicated |
| 422 | Invalid request values, types, thresholds or query parameters |
| WebSocket close 1008 | Session/origin policy failure; authenticate or use the correct origin |

HTTP errors use `detail`, which may be text, validation entries or an object with
constraint violations. A solver failure is also reported through job status and
`replan.failed`; the original HTTP incident request can already have succeeded.
