# Verification and requirement coverage

This is a demonstration and advisory system, not a replacement for certified railway safety systems. Automated invariants and mock demonstrations do not establish real-world railway safety.

## Repeatable checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark.py
# With a running server:
.venv/bin/python scripts/browser_check.py
# Alternative with installed Firefox and Node 22+:
node scripts/browser_firefox.mjs
.venv/bin/python scripts/stream_check.py
```

The original backend scenarios use the actual preserved graph. New block regressions additionally use small test-only straight corridors to isolate 500 km separation, block direction and signal behavior; these fixtures are never used by the application. Tests cover opposite-direction passing, siding overtaking, importance/commitment preservation, FIFO waiting protection, oversized consists, closures with/without a detour, failed signals, occupied-section braking, rear clearance, speed/acceleration, intermediate dwell and changed arrival targets, ten-incident bursts, seeded reproducibility, replay, quality, persistence, validation, authentication, access roles, idempotency, stale ingestion, report export and unchanged original-file delivery. Additional safety regressions cover wait-for cycles and stopping behavior. `tests/test_following.py` covers four simultaneous following trains on the reported V30979–V33999 corridor, nearby origins, rear clearance, signal waits and automatic resumption, opposing admission beyond a partial authority, retained grants during incidents, signal aspects, timetable pacing and section-clearance ETA estimates.

Browser verification uses a fresh profile and exercises login, scenario loading, speed/start/pause, map overlays, actual connection loss, backoff/resync, ten incidents, clearance, replay lockout, report data, stale-version rejection, desktop and mobile layout. The Firefox fallback severs actual HTTP sockets through a local test proxy, then restores service; it does not simulate success by reloading the page. Generated browser evidence records the actual browser used.

## Kazakhstan block-model update · 1 October 2026

**68 tests passed** in 51.33 s, with one existing test-client deprecation warning. New coverage includes same-direction departures 500 km apart, several trains sharing separate blocks of one original edge, opposite-direction canonical resource identity, complete rear clearance, unchanged grants through incidents/settings changes, braking-distance lookahead, correct occupied/red and clear/yellow/green signals, internal signal failure validation, and prevention of live block-layout replacement. Existing passing, overtaking, closure, following, deadlock and physics tests also pass. Updated fixtures seed near-terminal consists explicitly instead of assuming initial authority extends to the destination.

A separate updated server and fresh Firefox 155.0 profile passed desktop/mobile browser checks: no JavaScript errors or horizontal mobile overflow; reconnect, replay lockout, report content and stale-event rejection succeeded. UI receipt-to-render p95 was 17 ms (35 samples); map acknowledgment p95 was 15 ms (28 samples). Browser evidence is in [ktz-browser-results.json](../artifacts/ktz-browser-results.json); test evidence is in [ktz-test-results.json](../artifacts/ktz-test-results.json). The original measurement records below are retained as historical evidence.

## Measurement records

The original local run used Python 3.14.4 and Firefox 155.0 at 1512×1100 and 390×844. All **36 automated tests passed**. All four 3,500-second simulation scenarios completed with **zero detected constraint violations**. These checks validate the modeled constraints and supplied scenarios, not real railway operations.

The following-model update passes **45 automated tests**, including nine new following regressions. Both the same-origin and nearby-origin four-train cases achieve simultaneous movement before the leader arrives. The browser measurements below belong to the original run and were not repeated for this backend change.

| Measurement | Observed result | Scope |
|---|---|---|
| UI event receipt → DOM update | median 3 ms; p95 15 ms; maximum 396 ms | 35 browser samples |
| Map message → acknowledgment | median 3 ms; p95 7 ms; maximum 407 ms | 28 adapter samples, measured separately |
| Replanning | maximum 11.60 ms | Live SQLite-backed demo |
| Ten-incident batch | 14.08 ms | Live SQLite-backed demo, one burst |
| Ten-incident batch | median 0.574 ms; p95/max 0.605 ms | 20 separate in-memory trials |
| SSE delivery cadence | 1.967 Hz; median interval 508.11 ms | 10 steady localhost intervals |
| Browser reliability | Reconnect/resync, replay lockout, report data and stale-event rejection passed | No browser errors or mobile document overflow |

Chromium could not be downloaded in this environment; browser verification was completed with Firefox through WebDriver BiDi. The report check verifies CSV response content; it does not exercise an operating-system save dialog.

- `artifacts/backend-results.json`: OS/Python, wall time for 3,500 simulated seconds per scenario, observed arrivals and quality, zero/nonzero constraint violations, twenty ten-incident burst measurements using an in-memory journal.
- `artifacts/browser-results.json`: receipt-to-DOM-update and map-message-to-acknowledgment distributions, browser failures, reconnect/replay/report evidence and **live SQLite-backed** service metrics.
- `artifacts/test-results.json` and `artifacts/realtime-results.json`: test totals, SSE cadence, monotonic versions and full-state reconnection evidence.
- `artifacts/dashboard.png`, `timetable.png`, `mobile.png`: browser output for visual inspection. The existing “None – tracks only” basemap option is selected because the external tile provider blocked requests in this environment.

Measurements are local prototype measurements. In-memory batch timings and live SQLite timings are deliberately reported separately. UI timing begins after receiving an event, not at server creation. Map acknowledgment measures adapter execution; it is not a display-hardware photon measurement. Network disconnection gaps are not hidden by claiming the simulation clock stopped.

## Coverage and boundaries

| Requested capability | Implementation and boundary |
|---|---|
| Unchanged map | Four original file hashes checked. Runtime script adds overlays and menu controls only. |
| No invented topology | Existing adjacency only; synthetic healing excluded; suspected gaps never routable. Original source itself lacks verified shared-node IDs/interlocking metadata. |
| Moving trains and configuration | Connected oriented routes, independent 1–10 importance, physics, schedules, dwell and properties. Boundary staging/terminal withdrawal are explicit abstractions. |
| Conflict resolution | Exclusive logical block and vertex resources, bounded rolling movement authority, compatible direction commitments, length clearance, stable grants, waiting protection, safe deadlock hold. Not a global optimal/deadlock-free solver. |
| Stations and platforms | One conservative resource per mapped station vertex. No surveyed platform count or platform length supplied. |
| Passing and overtaking | Verified use of existing siding E47083 and bypass; exact oversized-train rejection. Single-edge holding points; automatic arbitrary yard shunting is absent. |
| Signals and switches | Functional direction-specific signal overlays and vertex route locks. Actual signal locations and certified switch matrices are absent. |
| Incidents and recovery | Five kinds, targeted assets, schedules, manual/duration clearance, random seed and ten-item batch. Committed routes retain authority while braking; no instant stopping. |
| Alternatives | Hold versus compatible existing detour before commitment; affected trains, ETA/delay/recovery and schedule-quality forecast. Not full counterfactual simulation or mid-motion route replacement. |
| ATO | Separate limit/braking/authority/arrival envelope, recommended versus actual speed, modeled kWh. Simplified resistance/kinetic model, no gradient/regeneration. |
| Quality index | Real rolling samples, transparent five-factor contributions/thresholds. Capacity is a demand-progress proxy; exact simulated stops are not field accuracy. |
| Dispatcher UI | Live map, train state/reasons, timetable, diagram, quality, incidents, alternatives, ATO, logs, connection/staleness and replay. Distances are from individual origins, not shared chainage. |
| Realtime | 2 Hz full-state SSE, bounded queue, versions, reconnect/backoff/resync, deduplication and stale-source rejection. Snapshot coalescing can skip intermediate visual states; transitions persist in journal. |
| Backend | Separated Python services, in-process event bus, REST and OpenAPI, auth/admin roles, persistent config/history, health/metrics. Single worker only. |
| History/reports | Configurable 24–72 h wall-clock retention, 15-minute read-only replay windows, searchable before/after journal, CSV export. No PDF report is claimed; CSV satisfies the requested CSV-or-PDF choice. |
| Presentation | 12 editable PowerPoint slides and a browser slide companion, including measured results and limitations. |

## Remaining limits

The source map cannot prove operational track connectivity, platform availability or signal placement. Synthetic links are quarantined rather than repaired. Long edges now have assumed block subdivisions; missing interstation and station receiving-track data still require conservative full-leg opposing/crossing direction locks and can cause remote waits. Signal-failure incidents conservatively close the entire parent map edge. See [the rule-source mapping](KAZAKHSTAN_RULES.md). A delayed or late closure can be encountered during unavoidable braking within protected authority. General multi-stop deadlock recovery, real sensor noise processing, surveyed infrastructure ingestion, live-state restart restoration, production authentication, distributed scaling and certified safety assurance are not implemented.

The tested operational workload is small: the supplied two-train scenarios, four following trains and bursts of ten incidents. The configured 100-train bound is a validation limit, not a demonstrated capacity claim. At excessive computational load, simulation time accumulates in a visible backend backlog rather than skipping physics steps.
