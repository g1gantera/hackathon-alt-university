# Stage 3: autodispatcher

This stage develops the existing CP-SAT planner and adds an inspection panel.
The scope remains the simulated Astana–Kokshetau corridor: 8 trains, 6 stations,
5 single-track sections, and 40 scheduled movements. Infrastructure capacities
and operating margins are demo assumptions, not certified railway rules.

## Check the website

### Separate stage-3 website

Open **http://127.0.0.1:8001/stage3.html** for the current local server (use port
8000 with the default Docker/local README configuration). The main dispatcher
also links to it with **Отдельное демо**. Vite dev mode serves `/stage3.html` too.

This is a separate frontend entry with its own styling, controls and playback.
It uses stateless demo endpoints and a separate solver worker, without changing
the main simulation, active plans, speed or history.

1. Choose opposing trains, same-direction trains, or all eight trains.
2. Set priorities from 1 to 10 and choose the priority policy.
3. Click **Рассчитать безопасный план**. Compare conflicting uncoordinated requests
   on the left with the independently validated schedule on the right.
4. Expand the conflict list and inspect the reservation charts. Bar widths include
   tail clearance, not only head arrival.
5. Click **Проиграть план**. Pause, rewind with the slider, change playback speed,
   or return to the beginning. The train rows share the same railway section;
   each row visualizes one train's position, not a separate physical track.
6. Inspect the decision table, then change priorities and calculate again.

The sandbox uses the same stage-3 solver, validator, and physics profiles.
Playback interpolates sampled positions from the calculated profiles. It is a
local replay of a complete schedule, rather than a live telemetry stream.
Changing inputs clears the old result. If the solver falls back to a conservative
schedule, the page explicitly labels the result as a heuristic.

The original main-scenario IDs and fixed departure anchors are not used in this
sandbox; demo IDs start with `D`. Requests all begin at model time 08:00 to make
resource competition visible. Demo schedules cannot be applied to the main fleet.

Endpoints (viewer access or demo mode):

- `GET /api/stage3/scenario?scenario=opposing|following|fleet`
- `POST /api/stage3/solve` with `{scenario, policy, priorities}`. Policies are
  `balanced` and `passenger_priority`; priorities map demo train IDs to 1–10.
  Concurrent calculations return 409 until the demo worker is free.

### Main dispatcher panel

1. Open the running website and refresh with **Ctrl+F5**.
2. Scroll to **Автодиспетчер**. The active schedule should pass validation.
   Zero conflicts is expected for the initial, already coordinated timetable.
3. Each row shows a train's next movement, priority, delay penalty weight,
   departure time, remaining wait, and earlier reservations where relevant.
4. Click **Пауза и расчёт**. The simulation pauses and the backend calculates
   candidate schedules; live WebSocket updates continue during calculation.
5. Under **Варианты перепланирования**, click **Сравнить график**. The dispatcher
   panel switches to that candidate, clearly marked as an unapplied preview.
6. Expand **Полное расписание** to see the order on all five sections, including
   departure, arrival, and tail-clearance times. One reservation's release must
   be no later than the next reservation's departure.
7. Click **Применить**, then **Запустить**. The server revalidates the candidate
   against current conditions before making it active. A stale or invalid plan
   is rejected; the previous plan is retained.

Paused calculation is useful for comparison. The original **Рассчитать варианты**
button can still calculate while running; these candidates may become stale
as model time advances. No candidate is applied automatically.

## Rules and priority

Hard constraints checked by an independent validator:

- Every train has exactly its required route legs, with valid whole-second times.
- No overlapping reservations on a section, in either direction, including tail
  clearance (`ceil(train_length_m / 5) + 15` seconds after arrival).
- No overlapping station-throat movements; each entry/exit reserves 15 seconds.
- Station track capacity is respected during waits and at origin/destination.
- Minimum running times, train readiness, and 90-second intermediate dwell hold.
- Existing delay, closure, and signal constraints remain supported.
- Started movements cannot change, including slower running times and extended
  tail-clearance reservations. New departures cannot be scheduled in the past.

The balanced objective penalizes each leg's lateness from the baseline, using
`train.priority × settings[type_weight]`. Defaults are 3 × 3 = 9 for passenger
and 1 × 1 = 1 for freight. The passenger-priority variant multiplies passenger
penalties by another 3. Internally the weights are integer-scaled by 10.
The objective also penalizes timetable changes and unnecessary station waits.
These are soft priorities: a higher-priority train does not override an occupied
section or automatically go first in every global solution.

The displayed predecessors are evidence from reservations before that departure,
not a claim that one train or priority was the sole cause of an optimizer choice.
Scheduled waiting is measured from readiness/mandatory dwell; it differs from
delay against the baseline. Remaining waiting is measured from current model time.

The solver can return a proven optimum or a feasible result within its time
budget. A validated schedule preserving the old order can be retained as a seed;
a conservative priority-order fallback is also available. Heuristic results are
labelled accordingly. If no validated schedule is found, no replacement is offered.
This does not prove that no feasible schedule exists.

## API

- `POST /api/replan`: dispatcher role; calculates candidates asynchronously.
- `GET /api/plans`: viewer role; candidates, baseline, and active plan.
- `GET /api/dispatch`: viewer role; validates and explains the active plan.
- `GET /api/dispatch?plan_id=...`: inspects a candidate or the active plan.
- `POST /api/plans/{id}/apply`: dispatcher role; revalidates before replacement.
- `state.updated` and `/api/state` include `dispatch`: current validation results,
  policy, and the next decision for each train. This also works in new history snapshots.

The detailed report contains `valid`, `conflicts`, `decisions`, `next_decisions`,
`section_order`, `active`, and `applicable`, plus the model time and state version.
Section conflicts identify opposing/following trains and overlap intervals;
switch, station-capacity, closure and other constraint failures have separate codes.
`applicable` is a point-in-time assessment; application always checks again.

Actual dashboard conflict counts now come from current independent validation,
rather than trusting the plan's saved `violations` field. A start request with an
invalid active schedule is rejected. An existing incident hold still allows
already-entered trains to clear their sections while new departures remain held.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
node --experimental-strip-types --test frontend/tests/realtime.test.mjs
pnpm --dir frontend build
.\.venv\Scripts\python.exe scripts/smoke_dispatch.py http://127.0.0.1:8001
```

The smoke check pauses the local demo, calculates and inspects both variants,
applies a valid plan, advances the simulation briefly, and leaves it paused.
Tests inject conflicting schedules, exercise exact clearance boundaries and
invalid times, compare priority decisions, verify committed timing preservation,
and check permission and apply-time rejection behavior.

Automatic incident replanning is implemented in [stage 4](replanning.md) on the
main dashboard. Explicit stage-3 calculations still produce manual proposals,
and the separate stage-3 sandbox remains isolated.
