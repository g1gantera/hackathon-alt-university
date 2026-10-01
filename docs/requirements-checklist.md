# Requirements and verification status

Updated **2026-10-02**, using the user-provided
`Автодиспетчер_презентация 1.10.pptx` (11 slides). Slide references below are file
positions, not the deck's printed footer numbers. Source SHA-256:
`2c774b0648f32cda7b1f9116fd7a0faffa8362d924b0694b4e31f74eded8b67c`.
Text was extracted from slide XML; the deck's visual layout was not reviewed in
this documentation stage. The deck supplies requirements, not executable instructions.

**Eight functional stages and the identified technical fixes are complete.**
The [latest verification](compliance-verification.md) records independent ingestion
services, compressed history, accelerated retention checks, visible browser paint
measurements and the reset/reconnect fix. The team presentation remains deferred
at the user's request. The [runbook](runbook.md#short-manual-walkthrough) provides
the live demonstration sequence; presenting it to the jury is a team activity.
There is no defensible single completion percentage: implemented behavior,
measured performance and submission artifacts have different evidence.

## Functional coverage

“Implemented” means code and relevant automated/live checks exist. It does not
mean certified railway control or that every nonfunctional threshold is verified.

| Stage / requirement | Slides | Status and scope | Evidence / where to check |
| --- | --- | --- | --- |
| 1. Map, stations, rails, moving trains and direction/status | 4, 9 | Implemented: full country line layer, six demo stations, eight trains; simulation only Astana–Kokshetau as requested | [README Step 1](../README.md#step-1-railway-section-map), map/network APIs, parser and map tests |
| 2. Position, speed, delay, route, signals/switches and schedule over WS/SSE | 4, 5 | Implemented with mock data and WebSocket; target 2 Hz | [Realtime contract](realtime.md), `test_realtime.py`, `test_realtime_timing.py`, realtime smoke |
| 3. Conflict detection, priorities and pass/wait scheduling | 4, 5 | Implemented: OR-Tools candidates, fallback, independent validation | [Dispatch guide](autodispatcher.md), `test_autodispatcher.py`, constraint tests |
| 4. Delay/signal/closure replanning and comparison of each alternative | 4, 5 | Implemented: auto/manual modes, per-candidate forecasts, elapsed calculation time, quality impact and application history | [Replanning guide](replanning.md), `test_replanning.py`, `test_replan_comparison.py`, live replanning/comparison smoke |
| 5. Recommended speed profile, ETA and energy advice | 4, 5, 9 | Implemented: shared physical profile and bounded economy heuristic; forecasts are labelled | [README Step 5](../README.md#step-5-speed-and-energy-advice), `test_speed_advice.py`, speed-advice smoke |
| 6. Quality index, top factors, runtime formula/weights and thresholds | 6 | Implemented: five components, two aggregation formulas, configurable normalization and categories | [README Step 6](../README.md#step-6-movement-quality-index), `test_quality.py`, `test_quality_settings.py`, quality-settings smoke |
| 7. Short-term history and rewind 5–15 minutes | 7, 9 | Implemented: 5/10/15 model-minute playback; retention defaults to 48 real hours, configurable 24–72 | [History guide](../README.md#steps-78-history-playback-and-report-export), `test_history_reports.py`; expiry logic tested, no 72-hour soak claimed |
| 8. Mini-report export | 9 | Implemented as CSV, satisfying the PDF/CSV alternative | [API history/export](api.md#history-and-csv), CSV tests and history-report smoke |

Model qualifications: capacity is a windowed occupancy/progress proxy; station
and signalling infrastructure is synthetic. Final-arrival timing is scored;
there is no separate measured stopping-distance error KPI. Economy advice is a
feasible heuristic, not a proof of globally optimal energy use. The advisory
prototype boundary follows slide 3.

## Architecture and nonfunctional requirements

| Requirement | Slides | Status / evidence | Remaining verification or qualification |
| --- | --- | --- | --- |
| Reception and normalization | 7 | Three separately runnable services validate movement, infrastructure and timetable channels before snapshot publication/archive; four-process native deployment tested | Source observations are mock data, expressly allowed; real railway sources would need adapters |
| Event bus or imitation | 7 | Implemented imitation: typed events, bounded per-client queues and WebSocket fan-out | In-memory fan-out has no durable broker replay; reconnect obtains a full snapshot |
| Scheduling, ATO, REST/WS dashboard | 7 | Implemented; responsibilities and data flow documented | [Architecture](architecture.md) describes actual process boundaries |
| Relational/time-series history, 24–72 h | 7 | SQLite, default 48 h; compressed payloads; 24/48/72-hour boundaries, idle expiry and restart/export identity verified with a controlled clock; 1,801-frame window queried in 20.03 ms | A real 72-hour soak is still not claimed. Dense 2 Hz storage projects about 1.73 GB/48 h plus WAL/events; see latest report |
| Visible UI update <500 ms from event receipt | 8 | Browser Element Timing measured visible dashboard text and map labels, maximum **76.7 ms**, including 5/10-incident samples | From message-handler entry to browser render timestamp; does not measure OS display hardware, queueing before the handler, all pixels or cold map downloads |
| Conflict-free replanning ≤5 seconds | 8 | **15 new four-service cases passed**, max **1.3737 s**, zero resulting conflicts, max state interval **588.03 ms** | Includes delay, signal, closure and five/ten incident bursts; previous 20 embedded cases remain historical evidence, not a guarantee for arbitrary workloads |
| 5–10 simultaneous incidents without UI slowdown | 8 | Repeated incident bursts and browser samples passed; map/menu/train/quality controls remained responsive | Bounded local samples and interaction checks, not multi-client capacity certification |
| Basic authentication and restricted settings | 8 | Viewer/dispatcher/admin browser login, restricted controls, administrator save and logout passed; API permissions tested | Freshness bug found and fixed; demo mode deliberately bypasses login |
| OpenAPI/Swagger | 8 | Generated `/openapi.json`, `/docs`, `/redoc`; [API guide](api.md) adds response/WS semantics | Dynamic dictionary responses are not fully modelled in OpenAPI; use linked contracts/types |
| Short architecture diagram | 8 | **Completed in this stage**: component/process diagram and replanning sequence | [Architecture document](architecture.md) |
| UI readability and accessibility | 10 | Browser workflow, desktop/narrow-window layout, keyboard tab activation and read-only archive controls checked | Not a full WCAG audit; small map labels can overlap near colocated trains |

## Historical evidence before these fixes

The preceding implementation stage completed these local checks on 2026-10-02:

- **157 Python tests**, **20 frontend tests**, TypeScript/production build.
- Component markup rendering with current and older archive data. This is not
  browser layout or paint verification.
- Automatic/manual replanning with all three incident types; started movements
  preserved; nonrecommended candidate selected, compared and applied correctly.
- Applied comparisons matched WebSocket updates, history and CSV after reset.
- Transport smoke: mean state interval **502.27 ms**, maximum **553.46 ms** in
  normal sampling; maximum **527.68 ms** during ten incidents. At 7x, twelve
  half-second intervals advanced **42 model seconds**. Final conflicts: **0**.

These are observations from the earlier local run, not fresh benchmark results
from this documentation stage. Repeat on the demo machine for final acceptance.
The historical [live-smoke-result.json](live-smoke-result.json) is an older,
smaller scenario and does not establish all of these requirements.

This documentation stage verified **60 local links/anchors**, **31 non-auth REST
operations** against the running OpenAPI schema, and the documented scenario/data
counts. The dashboard, stage-3 page, Swagger, ReDoc and schema returned HTTP 200.
All **20 PowerShell code blocks** parsed without syntax errors; this is not a
claim that every install command was executed. The documented `pnpm --dir frontend
test` command passed **20 tests** and is now included in CI. Workflow/Compose YAML
parsed successfully and matched the documented ports/database. Docker is not
installed in this environment; a container build/start has not been verified.

## Submission artifacts

| Slide 11 deliverable | Status |
| --- | --- |
| Source repository and startup instructions | Lockfiles, tests, four-service launcher, CI and [runbook](runbook.md) are included; publication status is recorded in the latest task delivery |
| 10–12-slide team presentation with architecture and quality formula | Deferred at the user's request; supplied case deck is the requirements source |
| Recorded demo or live defense | Working live application and reproducible walkthrough available; jury presentation/recording remains a team activity |

## Prior local stage outcome

Completed with **157 Python tests**, **21 frontend tests**, a production build,
sequential live scenarios, browser role/history/report/reset walkthroughs, and
automatic reconnection after an actual server restart. The browser found a stale
status bug after delayed login/reconnection; it was fixed and regression-tested.
Timing diagnostics are opt-in and do not alter the normal dashboard.

The [prior report](final-verification.md) and [prior measurements](acceptance-results.json)
are retained for traceability. The [latest report](compliance-verification.md)
supersedes their ingestion, storage and browser-proxy qualifications. Docker runtime,
a real 72-hour soak and physical display timing remain unverified. Measured local
prototype compliance is not a guarantee of production railway performance.
