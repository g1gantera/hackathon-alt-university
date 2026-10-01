# Repeatable demonstration

This is an advisory teaching prototype, not certified railway safety equipment. Times are simulation seconds.

## Existing demo infrastructure

The loop is discovered from the preserved source graph near the Karakultas / Üşbïik area:

| Asset | Existing identity | Length |
|---|---|---:|
| West boundary | V39875 | — |
| West approach | E47073, direction 1 | 13,480.26 m |
| Siding | E47083, direction 0 | 997.44 m |
| Main bypass | E47072 → E47071 → E47070, direction 1 | 996.99 m |
| East approach | E47069, direction 1 | 12,053.12 m |
| East boundary | V39870 | — |

Boundary labels remain “Track point V…” because those exact vertices are not station nodes. No station is renamed. Siding usable length is 947.44 m with 25 m clearances; demo trains are 180 m. A 980 m train is rejected for this siding.

## Cases

**Passing:** load `passing`, then Start. `KZ-101` departs at 0, importance 3, maximum 70 km/h; it holds inside E47083 for at least 140 s. Opposing `KZ-009` becomes eligible at 750, importance 9. It cannot reserve the west approach until the local's rear clears. The express crosses on the bypass while the local waits, then both finish. Measured arrivals are in `artifacts/backend-results.json`.

**Overtaking:** load `overtaking`. Both move west→east, express eligible at 90. It waits for the local to clear the approach, reserves the bypass and east section, and overtakes. Since original edges are exclusive, the local waits until the express clears the long east section/terminal. The benchmark runs 3,500 s to finish both services.

**Importance/fairness:** load `priority`. `KZ-010` (importance 10) obtains the shared route before `KZ-201` (2). The lower-importance train progresses after release. Increase its importance mid-run and verify the other train's committed locks remain. The starvation test separately gives a low-importance train >300 s eligible waiting and proves it obtains the next feasible slot ahead of a newly eligible high-importance service.

**Closure with a detour:** load `closure`. E47071 closes before commitment. The existing siding supplies an alternate path; the recovery panel compares holding and detour forecasts before dispatch commits resources.

**Closure without a detour:** on an empty run, close E47073, then create a service from V39875 to V39870. It stays staged because its immediate approach is closed. Clear the incident to obtain authority.

**Signal failure/recovery:** on a passing run, fail `SIG-47073-1` with manual clearance. If already committed, the local brakes inside the reserved section and holds. Clearing restores motion. The automated entry test fails the signal before admission and verifies red prevents entry.

**Ten simultaneous incidents:** while running, use **Inject 10 reproducible delays**. Types, targets and durations are deterministic in one atomic request. Backend batch timing and UI timing are measured separately. To demonstrate stochastic reproducibility, set admin random frequency/seed and repeat the same scenario and time steps.

**Replay/export/reconnect:** advance 600 s, pause, load ten-minute replay and scrub/play. Controls are disabled and the banner distinguishes replay. Return live, export CSV. The browser verification actually goes offline, detects loss, reconnects and receives a fresh snapshot without reload.

## Five-minute pitch

1. Explain immutable topology and uncertain infrastructure (30 s).
2. Run passing at 120×, inspect siding hold and route locks (40 s).
3. Create/clear a breakdown; show synchronized occupancy and explanation (40 s).
4. Change importance and inspect the measured quality factors (40 s).
5. Show timetable, driving advice, replay and CSV (60 s).
6. Present measured results and conservative-capacity/missing-platform limitations (40 s).
