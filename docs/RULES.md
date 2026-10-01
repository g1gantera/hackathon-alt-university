# Dispatch, signaling, energy and quality rules

This is a partial simulation of Kazakhstan automatic-block principles, researched on 1 October 2026. See [official sources and paragraph mapping](KAZAKHSTAN_RULES.md). Exact KTZ operation also requires actual block systems, station technical-administrative plans and local instructions, which are absent from this graph.

Distances are metres, speeds m/s internally and km/h in the UI, time simulation seconds, mass tonnes, energy kWh. Forecasts never authorize an infeasible movement.

## Mandatory constraints

Only the supplied graph adjacency is used. A visual crossing is not connected unless the original graph already contains a shared vertex. Synthetic ways and urban rail categories are excluded. `oneway` is respected; absent directions are bidirectional. Consecutive headings may turn ≤90°, matching the original strict route model. Routes cannot reuse an edge or reverse. No missing track is repaired.

Track occupation uses exclusive **logical signalling blocks**, independent of the route's final destination. Long original edges are divided uniformly into `ceil(edge_length / signal_block_m)` blocks (default maximum 2,000 m); shorter edges remain single blocks. The overlay creates no new track or connections and does not modify the graph. These signal locations and lengths are assumptions, not surveyed KTZ infrastructure or a rule prescribing 2,000 m. Short source edges can be shorter than the braking-distance spacing required for a real installation; the speed envelope independently enforces a stop inside authority.

A vertex remains one exclusive junction/platform resource. The dispatcher reserves a clear local prefix, ending at the next scheduled stop, a block boundary, or c metres before the first unavailable block/junction. The requested lookahead is the greater of the configured minimum (default 5,000 m) and `v_max² / (2b) + 1.2 v_max + c`, covering service braking, one planning interval plus integration margin, and clearance. It rounds forward to a block boundary and stops c metres before the following block. Thus a 500 km trip does not acquire 500 km of exclusive reservations. Actual authority can be shorter when an obstruction intervenes; the speed envelope then limits acceleration and braking accordingly.

At head position x, length L and clearance c, a resource behind the train releases only when `x − L − c` passes its end. Following trains can enter released blocks within the same original map edge while the leader is still running. Movement authority extends on replanning and never shrinks; a previously granted braking envelope stays protected. Reaching an intermediate authority boundary means waiting at a signal, without recording an arrival or advancing the stop index. Occupation and reservation are reported separately in reasons, block overlays and train details. Initial trains wait in staging. Admission places the head at the origin with any negative rear distance outside the modeled boundary. Arrived trains retain their footprint until simulated consist withdrawal after terminal service (default 30 s). Block size and clearance cannot change while a train holds track.

Switch positions are original incoming/outgoing edge IDs. Vertex locks exclude incompatible routes. A preference or incident cannot revoke authority or move points beneath a train. Platform inventory is absent: station capacity is conservatively one mapped vertex. A siding hold places the head c metres before the edge end; fit requires `L + 2c ≤ edge length`. This clears both junctions and leaves a real parallel path usable. Multi-edge siding lengths and additional platforms are not inferred.

**Remaining limitation:** direction commitments conservatively protect the remaining leg through the next scheduled stop, including sections beyond current movement authority. They may be shared only by compatible movements: the same directed edge, or a shared directed approach/departure at a junction. Opposing edges and crossing junction movements prevent admission until the commitment clears; they can still use a feasible existing detour. At a scheduled dwell only the occupied footprint is retained, allowing a train that fits inside a siding to let opposing traffic pass. A direction commitment alone never authorizes motion or displays a proceed signal. Wait-for cycles are detected and held. There is no reversing or forced lock release; unusual multi-stop arrangements can remain deadlocked pending clearance or terminal withdrawal. Fairness applies only when a compatible route becomes available. Real opposing protection applies to shared interstation sections and prepared station routes, not an arbitrary scheduled passenger stop. Mapped station vertices do not establish usable receiving/passing tracks; until those are supplied, remote opposing/crossing waits can still occur. Never shorten these direction commitments to the rolling braking horizon: that can admit opposing trains into a corridor with nowhere to pass.

## Dispatch preference

This is a simulator heuristic, not a verified KTZ train-priority order. Among feasible candidates:

```text
P = wI × (importance − 1)/9
  + wD × min(predicted_positive_arrival_delay_s / 300, 1)
  + wW × accumulated_eligible_wait_s / 300
  − wE × min(estimated_remaining_traction_kWh / 1000, 1)
```

Defaults: `wI=4, wD=2, wW=3, wE=1`. Terms are dimensionless. Importance is independent of category. Delay caps at five minutes; waiting is unbounded. Only time eligible to move counts, excluding scheduled pre-departure and required dwell. After 300 s eligible waiting, a protected FIFO tier precedes ordinary scoring. It sorts by greatest wait; other trains sort by greatest score. Ties use earliest departure, then lexical train ID. Infeasible candidates do not prevent a feasible candidate from proceeding.

Remaining energy uses the model below at min(train maximum, 80 km/h), with one start and no gradient. ETA sums remaining lengths/current limits, approximate half acceleration time, dwell, known clearance waits and blocking movement time. Following waits estimate clearance of the next conflicting section, including train length and terminal withdrawal when necessary, rather than the leader's whole remaining trip. An applicable incident requiring manual clearance, including one affecting the immediate blocking train, gives unknown ETA. These forecasts do not account for all future traffic or propagate every downstream queue delay.

Before commitment, the router may replace a blocked path while preserving the occupied prefix and stops. Stable distance ordering and resource signatures prevent repeated equivalent plan changes. Once committed, the route remains unchanged until its next stop. Importance/timetable edits create new plan versions while preserving locks.

## Signals and incidents

| Aspect | Rule |
|---|---|
| Red | Protected block occupied, no compatible directional authority, failed/closed asset, another owner, behind the train head, or beyond the stop/incident boundary |
| Green | Matching direction and route owner, at least two consecutive clear authorized blocks, compatible junction locks and sufficient protected braking distance |
| Yellow | Clear authorized block with an upcoming closed signal/stop, insufficient distance for an unrestricted approach, or temporary speed restriction; prepare to stop within authority |

Signals are overlays at logical block entries, not surveyed equipment. Ordinary automatic-block aspects follow the distinction in signalling instruction §25; specialized station, turnout, four-aspect and degraded-operation indications are not implemented. Train speed uses the same authority and incident predicates as indications. Failed switch/station assets conservatively affect all adjacent edges; a failed signal closes its edge in both directions.

Delay/breakdown triggers controlled braking and hold. A closure ahead causes braking toward its entry. A train with any part of its consist already inside brakes while retaining its occupied blocks and all committed authority. That hold remains latched until clearance, even if braking carries the consist beyond a short affected block. A sudden incident inside physical stopping distance cannot stop it instantaneously; it may overrun the newly imposed boundary while stopping inside an already reserved envelope. Other trains cannot enter this envelope. Clearance restores normal advice and movement. All start/clearance times use the simulation clock.

Alternatives are evaluated before new commitment, stamped with time and next plan version, and revalidated by dispatch. Only actual existing detours are shown. When none can be used, the UI explains holding/clearance. Quality forecasts show the schedule-factor change versus immediate clearance or the current wait plan while holding other factors fixed. They are not measured improvements. Rerouting a committed moving train is not implemented.

## Advisory ATO

Advice considers train maximum, current/upcoming edge limits, temporary restrictions, braking distance, authority, next stop and arrival target. To reach future speed vₜ at distance d with braking b:

```text
v ≤ sqrt(vₜ² + 2 b d)
```

A discrete integration margin initiates braking early. Acceleration and braking bounds apply through rest; after stopping, the last ≤5 cm resolves to the exact simulated stop. No physical precision claim is made. Motion uses ≤0.2 s steps, with exact within-step distance when coming to rest. Spare timetable time reduces the advised cruise speed using distance to the scheduled stop, while the shorter movement authority independently limits safe braking. The demo follows this envelope; the future profile is advisory under present conditions.

## Simplified traction energy

```text
mass_kg = 1000 × mass_t
F_N = 0.0015 × mass_kg × 9.81 + 0.8 × v_mean_mps²
ΔE_kWh = [F_N × Δx_m
          + max(0, 0.5 × mass_kg × (v_new² − v_old²))]
         / (0.85 × 3,600,000)
```

This accounts for rolling/aerodynamic resistance and positive kinetic-energy changes at 85% efficiency. No gradient, curvature, auxiliary load, regenerative braking or locomotive traction curve. This is a teaching estimate, not measured electricity/fuel. Stop/restart incidents increase modeled energy.

## Movement quality index

Approximately one sample per simulation second, last **300 s** by default (configurable 60–900):

```text
Q = 100 × (0.30 S + 0.20 C + 0.20 E + 0.20 F + 0.10 A)
```

Factors range [0,1]. The UI shows factor score, weight and contribution. There is no score before observations. Factors with no demand, energy or stops are neutral (1); observed duration is disclosed.

| Factor | Definition |
|---|---|
| S: schedule | `max(0, 1 − mean positive predicted/actual arrival delay / 300)`, averaged across eligible services then samples. Completed services use actual arrival. Unknown manual-clearance forecasts use current lateness as a lower bound. |
| C: capacity proxy | Moving eligible train-samples / eligible train-samples. Excludes pre-departure, required dwell, terminal and completed trains. Measures operational demand progressing, not surveyed line capacity. |
| E: energy | `min(1, reference traction increment / actual traction increment)` over the window. Reference follows the same traveled distance at min(train maximum, local limit), plus one launch acceleration. Actual includes every restart. Zero consumption is neutral. |
| F: conflict freedom | `1 − fraction of sampled seconds containing incompatible ownership, off-route/excluded-track violation or unresolved wait-for cycle`. Safely refused requests are not unsafe conflicts. |
| A: stopping | `max(0, 1 − max absolute stop error_m / 5)` for arrivals in the window. Ideal deterministic stops are exact; no measured field accuracy is claimed. |

Arrival-time accuracy belongs only to S; A measures spatial error, preventing a duplicate arrival penalty. Capacity, energy and unresolved conflicts measure different consequences; congestion can naturally influence more than one. The UI names factors losing points; significant total changes are journaled. Thresholds default to Normal ≥80, Attention ≥55, Critical <55, with validated ordering. A favorable quality score never overrides a mandatory constraint.
