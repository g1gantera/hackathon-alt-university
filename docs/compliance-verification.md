# Technical fixes and verification — 2026-10-02

The identified implementation gaps are fixed. All eight feature stages remain
available: the full railway map, corridor simulation, realtime data, dispatch,
replanning, speed/energy advice, quality settings, history and CSV export.
Simulation remains limited to Astana–Kokshetau. Presentation work is deferred.

This report supersedes the ingestion, storage and rendering-proxy limitations
in the [earlier report](final-verification.md). Reproduce startup and checks with
the [runbook](runbook.md); machine-readable evidence is in
[compliance-results.json](compliance-results.json).

## What changed

- **Three independent ingestion services** validate movement, infrastructure
  (signals/switches) and timetable frames. The main application accepts only a
  complete batch with matching run/version. Mock observations remain owned by
  the simulator; the services validate these observations before WebSocket
  publication and snapshot archival. They are not external railway feeds.
- **A four-service launcher and Compose configuration** supply token-protected
  service communication, readiness checks and clear failure behavior. Native
  startup generates an ephemeral service token and stops its own children on
  exit. Embedded normalization remains available for lightweight development.
- **A reset/reconnect race is fixed.** A socket opened immediately after reset
  now waits for the new normalized revision. It no longer fails its handshake
  because the ingestion response is still in flight. If no fresh snapshot
  arrives within two seconds, it closes with a retryable 1013 code.
- **History is compressed without losing fields.** Query metadata remains JSON;
  larger bodies use zlib in a related table. Legacy records, saved comparisons
  and CSV remain readable. Paused heartbeats prune expired rows in bounded
  batches, with cascading removal of compressed bodies.
- **Browser diagnostics now observe actual visible text rendering timestamps.**
  Dashboard quality text and train labels opt into Element Timing. The normal
  website does not enable probes or remount labels. Diagnostics remain bounded,
  local-only and explicitly enabled with `?performance=1`.
- **CI checks retention and service integration**, alongside backend tests,
  frontend tests and the production build, including pushes to `M_part`.

## Four-service performance

Windows 11, Intel Core i5-10300H (8 logical processors), approximately 16 GiB RAM;
Python 3.12.14, Node 24.19.0, pnpm 11.25.0. Tests used an isolated database and
loopback deployment on 8012/8111–8113. The user database was not used by smoke tests.

| Check | Result |
| --- | --- |
| Backend and parser tests | **173 passed**; one upstream TestClient deprecation warning |
| Frontend tests / production build | 21 passed / built successfully |
| Incident cases | 15: closure, signal, delay, five incidents, ten incidents; three repetitions each |
| First incident request → applied plan received over WebSocket | Median 0.6944 s; maximum **1.3737 s**, below the 5 s limit |
| Validated resulting conflicts | **0** in every case |
| Largest sampled state interval | **588.03 ms**, maintaining at least 1 Hz |

A fresh Python environment installed the complete lockfile and passed `pip check`.
Starting all four services from that environment with `--env-file .env.example
--verify` passed five further incident cases: maximum applied-plan receipt
**1.5425 s**, maximum feed interval **664.86 ms**, zero resulting conflicts.
The supervisor stopped its children afterwards. A separate fresh frontend folder
installed the frozen pnpm lockfile, passed all 21 tests, and built the same main
JavaScript asset as the running website. The existing large-bundle advisory remains.
These are fresh dependency installations on this machine, not a different clean OS.

Sequential live checks against remote ingestion also passed: history exported
491 CSV rows, stayed read-only and survived reset; speed advice preserved terminal
ETA with 52.27 kWh forecast saving; a nonrecommended plan was compared/applied and
matched WebSocket, CSV and archive output. Existing functionality was retained.

The reset/reconnect regression is covered by the ingestion test and the repeated
live reset/connect sequences. Remote failure and recovery, authentication,
malformed frames and obsolete revisions have automated coverage. A failed remote
service does not silently switch the deployment to embedded mode.

## Visible browser rendering

Browser: Codex In-app Browser reporting Chrome 154.0.0.0. The diagnostic viewport
was 1280 × 2600 to keep both actual dashboard text and map labels visible while
incident rows expanded. Samples require a running simulation, a visible document
and a positive visible element area. Map counts are per train label, not
independent frames. Five/ten-incident groups overlap the incident group.

| Surface / condition | Samples | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Dashboard, normal | 35 | 31.0 ms | 52.9 ms | **76.7 ms** |
| Dashboard, incidents | 55 | 33.9 ms | 62.6 ms | 73.1 ms |
| Dashboard, ≥5 incidents | 24 | 33.9 ms | 68.7 ms | 73.1 ms |
| Dashboard, ≥10 incidents | 7 | 44.2 ms | 73.1 ms | 73.1 ms |
| Map labels, normal | 280 | 31.0 ms | 52.9 ms | **76.7 ms** |
| Map labels, incidents | 446 | 34.0 ms | 62.6 ms | 73.1 ms |
| Map labels, ≥5 incidents | 198 | 33.9 ms | 68.7 ms | 73.1 ms |
| Map labels, ≥10 incidents | 56 | 44.2 ms | 73.1 ms | 73.1 ms |

Measurement is `PerformanceElementTiming.renderTime` minus WebSocket
message-handler entry, including JSON parsing and React/DOM updates. Element
Timing supplies the browser's text rendering timestamp, as defined by the
[W3C API](https://w3c.github.io/element-timing/). Only newly rendered, visible
annotated elements produce samples; not every message is guaranteed a paint.
This strengthens the earlier double-animation-frame proxy but does not measure
queueing before the handler, physical monitor latency, every chart/tile pixel,
cold tile downloads or performance on arbitrary machines. No sampled paint
exceeded the 500 ms requirement; no browser warnings/errors were recorded in this run.

To repeat, open the isolated server with `?performance=1`, ensure measured text is
visible, run `smoke_performance.py`, then group console JSON `type: visible_paint`
by `surface`, `running`, `visibility` and `incidents`. Read `render_ms`; retain
sample counts. The older `snapshot`/`paint_opportunity_ms` messages remain clearly
separate. The probe emits at most 2,000 messages per page load.

## Retention and storage

The verifier uses a **controlled clock**, not a 72-hour wall-clock run. It writes
full snapshots every 30 virtual seconds across each retention period plus two
hours, then checks exact inclusive expiry boundaries, restart/export identity,
idle cleanup and removal of orphan bodies. Unit tests additionally cover mixed
legacy/new records and full payload restoration.

| Retention | Records written | Retained at boundary | Expired rows left / orphan bodies |
| --- | ---: | ---: | ---: |
| 24 hours | 3,121 | 2,881 | 0 / 0 |
| 48 hours | 6,001 | 5,761 | 0 / 0 |
| 72 hours | 8,881 | 8,641 | 0 / 0 |

All three cases preserved identical CSV after reopening the database and removed
all records when advanced beyond retention without new writes. Mean compressed
body size was about **4.27 KB** in these samples.

A separate dense 15-minute 2 Hz window wrote **1,801 snapshots**: metadata query
**20.03 ms**, median write **2.57 ms**, maximum write **20.61 ms**, database pages
**9,035,776 bytes**. Extrapolating these pages to sustained 2 Hz recording gives
about **1.73 GB/48 hours** or **2.60 GB/72 hours**, before extra events and WAL.
This is a sizing estimate, not a measured long run. Unchanged paused/completed
snapshots are not repeatedly archived. Deleted SQLite pages can be reused;
physical file shrinkage is not guaranteed. `/api/health` distinguishes allocated
and reusable bytes.

## Delivery and remaining verification limits

The code, configuration and tested live-demo walkthrough are ready for the local
hackathon prototype. This does not certify real railway control or global
optimality. Docker is not installed here, so its runtime has not been tested.
Compose structure and CI YAML were parsed and checked; the equivalent native
four-service deployment was exercised. The main website was restarted on port
8001 after a consistent SQLite backup of 11,655 existing records; history is
retained and the live simulation starts paused after restart.
A real 72-hour endurance run remains a separate operational verification; the
controlled-clock tests cannot prove long-run memory/resource stability. A jury
defense or recording and the deferred presentation are team deliverables.

![Current dashboard after the four-service restart](dispatcher-current.png)
