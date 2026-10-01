# Stage 2: realtime train data

The source is the existing simulated Astana–Kokshetau fleet, not GPS telemetry.
The Kazakhstan railway layer remains visible; only this corridor is simulated.

## Try it

1. Run the backend and open the website (see README for installation).
2. Click **Запустить**, select **30×** or **60×**, and select a train.
3. Its map marker and detail panel receive updates every half-second. The panel
   shows route, longitude/latitude, current speed, delay, station/section, and
   the server's snapshot time. The source is explicitly marked **СИМУЛЯЦИЯ**.
4. Click **Пауза**. Positions and model time stop; fresh snapshots still arrive.
5. Disconnect the network or stop the backend. Within five seconds the UI marks
   data stale and disables movement controls. Reconnection retries use delays
   of 1, 2, 4, 8, then 10 seconds. Restoring connectivity loads a full snapshot.
6. **Сбросить** starts a new simulation epoch at time zero. Reconnecting does
   not reset the simulation; restarting the backend does.

## API contract

`GET /api/trains` returns `{realtime, epoch, state_version, sim_time_s, trains}`.
`GET /api/trains/T01` returns the same metadata and one `train`; unknown IDs
return 404. `GET /api/state` retains the full dashboard state and adds `realtime`.
All endpoints use the existing viewer-or-higher authentication policy.

Train fields include:

| Field | Meaning |
| --- | --- |
| `coordinate` | `[longitude, latitude]`, WGS84; interpolated along railway geometry |
| `position_m` | Distance from the Astana end, including for reverse trains |
| `speed_mps` | Current simulated speed; multiply by 3.6 for km/h |
| `delay_s` | Largest nonnegative lateness over reached/overdue baseline leg arrivals |
| `route_name`, `route_station_ids` | Current corridor route, in travel order |
| `origin_id`, `destination_id` | Route endpoints |
| `station_id`, `section_id` | Current station or occupied section |
| `direction`, `bearing_deg`, `status` | Direction (+1/-1), compass heading, waiting/moving/completed |
| `eta_s` | Planned arrival in model seconds, or null while awaiting a plan |
| `position_source` | `simulation` |

`/ws` immediately sends a full `state.updated` event and targets **2 Hz** (every
500 ms), including while paused or after all trips finish. Existing
metrics and operational events share the same stream. The envelope contains:

```json
{
  "protocol_version": 1,
  "stream_id": "unique-per-server-start",
  "seq": 42,
  "observed_at": "2026-10-01T12:00:00+00:00",
  "source": "simulation",
  "update_interval_ms": 500,
  "type": "state.updated",
  "epoch": "unique-per-simulation-reset",
  "sim_time_s": 60,
  "state_version": 61,
  "plan_id": "active-plan-id",
  "payload": {"...": "full dashboard snapshot, including realtime metadata"}
}
```

`observed_at` is the UTC wall-clock sampling time. `sim_time_s` is time in the
model (the UI displays it starting at 08:00). All trains in a snapshot share
the sampling time. The embedded `realtime.seq` matches its state event.

The ticker uses monotonic deadlines. Snapshot calculation and database writes
consume part of the current half-second slot instead of extending the next wait.
The 2 Hz target leaves margin above the presentation's 1 Hz minimum; actual
delivery also depends on server load, network and the receiving client.

The time multiplier still means model seconds per real second: at 30× each
scheduled update advances 15 model seconds. Whole-second model timing is retained:
at 7× the steps alternate 3 and 4 seconds, and at 1× some frames share a model
time. Fractional time is discarded when a sampled pause, speed change, reset or
replanning hold changes the clock context. Missed deadlines during a long server
stall are skipped, with no catch-up burst or large model-time jump. Severe stalls
can therefore slow model progress; the clock does not replay unobserved movement.

The client rejects older REST responses, ignores duplicate socket events,
and reconnects on sequence gaps or a silent connection. Every new connection
loads current state; there is no lossless event replay. Slow clients have a
bounded queue and recover through a new snapshot. Server restarts change
`stream_id`; resets change `epoch` without resetting the sequence counter.
Active sockets also stop when their login session expires or is logged out.

Use a **single backend worker**: simulation and WebSocket fan-out are currently
in memory. A multi-worker deployment would need shared state and messaging.
Dispatch optimization and incident planning are existing functionality;
this stage establishes the realtime transport, not a new optimizer.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
node --experimental-strip-types --test frontend/tests/realtime.test.mjs
pnpm --dir frontend build
```

The frontend tests use Node 22.18+ (or Node 24) and its built-in test runner.
`python scripts/smoke_realtime_timing.py http://127.0.0.1:8012` checks measured
WebSocket cadence, the time multiplier, and ten concurrent incidents against a
separate local server. It resets that server's simulation before and after.
This measures transport delivery, not browser paint latency.
