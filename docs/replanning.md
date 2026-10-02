# Stage 4: automatic replanning

Open the main dashboard at **http://127.0.0.1:8001/** for the current local server
(port 8000 in the default Docker/README configuration). The separate `/stage3.html`
sandbox remains isolated and is not the incident-control interface.

## Try it

1. Refresh the dashboard with **Ctrl+F5**. Locate **Сбои и перепланирование**.
2. Leave **Автоприменение** enabled. Select balanced delay or
   passenger priority as the objective used to choose the recommended candidate.
3. Start the simulation at 30×. Add a section closure or signal failure. To add a
   train delay, choose a train waiting at a station; moving trains cannot receive
   this station-hold incident.
4. Watch the phases: queued → calculating → applied. The main graph, train ETAs,
   resource reservations and active plan switch together after validation.
5. Inspect the quality, conflicts and delay comparison, then expand **Все показатели
   и изменения расписания** for energy, arrival accuracy, terminal ETAs and changed
   movement times. Incident-induced delay may increase: the old plan is a reference,
   and may violate the new constraints. This is not a measured performance gain.
6. Click **Устранить** to end an active incident early and recalculate again.
7. Disable automatic application and add another incident. Calculation still runs,
   but the status stops at review. Preview and **Применить** a candidate manually.
8. **Сброс** also works during calculation: the new run has its own epoch,
   starts paused, and restores the default automatic mode and balanced policy.

If the model was paused, an automatically applied plan leaves it paused. Click
**Запустить** to move again. Incident durations use model time, so they do not
expire while the model is paused.

### Compare a selected alternative

1. Reset and leave paused. Turn off **Автоприменение**.
2. Add a ten-minute train delay to **№101** (`T01`), and wait for **Ожидает применения**.
3. Open **Диспетчер**. On any candidate card, click **Сравнить график**.
4. Read **Выбранный вариант**. It compares that exact candidate with the currently
   active timetable. The recommended candidate is labelled separately above.
   The graph uses the active plan as its dashed reference and the candidate as
   its solid line. The analytics chart still uses the original baseline.
5. Review all metric deltas (`new − old`), each train's final arrival, and changed
   departures, intermediate arrivals and tail release. Slower economy movements
   remain visible even if their departure and terminal arrival do not change.
6. Apply the candidate. **Результат применения плана** captures a fresh comparison
   at application time. **История** (header) preserves this result after
   settings changes and reset.

Both forecasts use the same evaluation time, operating constraints and quality
formula. The old schedule may be infeasible after an incident; repairing conflicts
can increase delay and decrease quality. The panel shows both effects without
calling the difference a measured improvement. Incompatible or missing formula
signatures suppress the quality delta in older archive data.

The comparison is a captured evaluation, not continuously recomputed live results.
Its model time and the new plan's last calculation duration are shown. Duration
is wall-clock time for the last solver attempt (or economy calculation); it does
not include incident debounce or earlier stale retries. The five-second tag refers
to that attempt. Use **Обновить оценку** in a candidate preview to evaluate again;
application always validates against the latest state. Late responses from another
candidate, epoch, active plan, constraint version or quality formula are ignored.
An archive retains its original formula and values. Older archives lacking new
timing fields show an unavailable value instead of reconstructing it.

### Custom speed

The fixed 1×/5×/15×/30×/60× selector has been replaced with a number field. Enter a
positive whole-number multiplier and click **Установить** (or press Enter).
For example, 100× advances 100 model seconds per real second (50 per 500 ms update). The same
control is available in the separate stage-3 playback page.

There is no 60× application cap. The representable range is 1 to
9,007,199,254,740,991× (the browser's largest exact integer). Large jumps process
scheduled departures in order without iterating through every elapsed second;
the clock stops at the last train's tail-clearance time when the schedule finishes.

Above 60×, the model clock waits during calculation or an incident-related plan
hold, so it cannot jump past the solver's planning horizon while awaiting a result.
The panel explicitly shows this condition. After automatic application it continues
at your selected speed. For manual review or a failed calculation, apply/retry a
plan, resolve the incident, or lower the multiplier to 60× or less to let model time
advance (including natural incident expiry). At 60× and below, already-moving
trains and the clock continue during calculation as before. Use 15× or 30× to
inspect incident handling visually. Playback on `/stage3.html` is local and does
not invoke the replanner when changing playback speed.

## Application rules

- All new departures are conservatively held while an incident requires a new
  plan. This is a global hold, not a claim of an optimized selective hold.
- Already-started movements keep their exact start, arrival and clearance times.
  In this demo, a train already inside a newly closed section is allowed to clear
  it. The model does not simulate emergency braking inside a closed section.
- A single coordinator combines requests arriving within 300 ms. If new inputs
  arrive during a calculation, its result is discarded and the latest state is solved.
- Candidate schedules are independently validated against the current state after
  the worker returns. The selected schedule is validated again immediately before
  installation, with no asynchronous gap between checking and replacing it.
- Reset, incident changes, mode/policy changes and settings changes invalidate
  affected pending work. Superseded results cannot overwrite a newer scenario.
- At speeds up to 60×, the running model receives planning headroom. Faster clocks
  are held during calculation and require no speed-proportional headroom. If only elapsed model time makes
  a result stale, the coordinator retries from fresh state, up to three attempts.
  A five-second calculation budget is a target for each attempt; elapsed time is
  reported, and feasible/heuristic plans are not labelled proven optimal.
- Solver failure, malformed results or no validated candidate leave the old plan
  installed and incident-related departure holds active. The UI exposes the error
  and **Повторить расчёт**. No unchecked result releases the hold.
- Natural incident expiry is recorded once. If a failed/manual hold remains and
  no calculation is active, expiry triggers another calculation using the chosen
  application mode. A previously applied plan already accounts for known expiry.
- Manual **Рассчитать варианты** continues to produce reviewable proposals even
  with automatic incident application enabled. It does not automatically install.

Automatic selection uses the existing stage-3 objective. It does not add energy
optimization or modify train physics. Model assumptions remain those documented
in the README and the stage-3 guide.

## API and live state

| Endpoint | Behavior |
| --- | --- |
| `POST /api/incidents` | Create delay, closure, or signal incident and queue recalculation |
| `POST /api/incidents/{id}/resolve` | Resolve an active incident early; repeated resolution is idempotent |
| `GET /api/replanning` | Read options and current job status |
| `PUT /api/replanning` | Set `auto_apply` and `policy` (`balanced` / `passenger_priority`) |
| `POST /api/replanning/retry` | Retry using the current mode and latest conditions |
| `POST /api/replan` | Explicit manual calculation and review |
| `GET /api/plans/{id}/comparison` | Read-only evaluation of the selected candidate versus the current active plan |
| `POST /api/plans/{id}/apply` | Validate and install a manually selected candidate |

All mutations require dispatcher or admin access. Viewer can read status. Local
demo mode retains the existing automatic admin role.

`/api/state` and WebSocket snapshots now carry `replanning_options` and
`replan_status`. Status contains the phase, job ID, trigger, attempts, calculation
time, recommendation/application ID and comparison. Incidents expose active,
expired or resolved status. These fields are also recorded in new history snapshots.

Comparisons include `basis` (`forecast_at_evaluation` or `forecast_at_application`),
`evaluated_at_s`, epoch/constraint identity, plan labels, calculation duration,
both forecasts, and preserved/total committed movements. Changed movements keep
old/new departure, arrival and tail-release times. The comparison endpoint returns
`applicable: false` when advancing time makes a candidate invalid, and 404/409 for
missing or invalidated candidates. Viewers can inspect comparisons but cannot apply.
CSV `replan_movement` rows export each changed time separately; `replan` details
include the evaluation time, calculation duration and saved formulas.

Events include `replan.queued`, `replan.started`, `replan.completed`,
`replan.failed`, `plan.applied`, `incident.resolved`, `incident.expired` and
`replan.options_changed`. The client uses full snapshots as the authoritative state
and can recover after connection loss. Events/plans use the existing SQLite store.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
node --experimental-strip-types --test frontend/tests/*.test.mjs
pnpm --dir frontend build
.\.venv\Scripts\python.exe scripts/smoke_replanning.py http://127.0.0.1:8001
.\.venv\Scripts\python.exe scripts/smoke_replan_comparison.py http://127.0.0.1:8001
```

Run the smoke scripts sequentially: each resets the demo before/after. They inject all three
incident types, verifies auto-application and preserved started movements, checks
early resolution and manual review, and leaves a fresh paused scenario.
Unit/API checks also cover competing requests, in-flight reset, turning automatic
mode off during calculation, stale-time retries, solver errors, expiry recovery,
role restrictions, candidate selection, comparison immutability, export consistency,
arrival-only economy changes and invalid-plan rejection.

For the component render check, save an optional fixture while running the live
comparison smoke, then render current and legacy archive data:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_replan_comparison.py http://127.0.0.1:8001 .logs/comparison-fixture.json
node frontend/tests/render-replan-comparison.mjs .logs/comparison-fixture.json
```

This verifies generated component markup, not browser layout or paint latency.
