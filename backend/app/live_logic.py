"""Live projections of native plans; no independent timetable or traffic model.

The spatial cells use the same constant acceleration and resistance assumptions
as advisory.speed. Native profile totals remain the authoritative energy budget.
"""

from bisect import bisect_right

import numpy as np


def prepare_profiles(scenario, plan, profiles):
    trains = {t.id: t for t in scenario.trains}
    sections = {s.id: s for s in scenario.sections}
    result = {}
    for move in plan.movements:
        key = f"{move.train_id}:{move.section_id}"
        profile = profiles[key].model_dump()
        if profile["status"] != "FEASIBLE":
            raise ValueError(f"Cannot animate an unreachable movement: {key}")
        train, section = trains[move.train_id], sections[move.section_id]
        points = profile["points"]
        x = np.array([p["position_m"] for p in points])
        v = np.array([p["speed_mps"] for p in points])
        from .advisory.energy import cell_energy
        times=np.array([p['time_s'] for p in points])
        gross,recovered=cell_energy(train,section,x,v,times,move.origin==section.station_b)
        if not np.isclose(sum(gross), profile["traction_energy_kwh"], rtol=1e-8, atol=1e-8):
            raise ValueError(f"Live energy disagrees with the native profile: {key}")
        profile["_traction_kwh"] = np.concatenate(([0.0],np.cumsum(gross-recovered))).tolist()
        profile["_times_s"] = [p["time_s"] for p in points]
        profile["_auxiliary_power_w"] = train.auxiliary_power_w
        result[key] = profile
    return result


def sample_profile(profile, elapsed):
    """Position and energy at a time inside a constant-acceleration spatial cell."""
    points, times = profile["points"], profile["_times_s"]
    t = max(0.0, min(elapsed, profile["duration_s"]))
    if t >= times[-1]:
        return points[-1]["position_m"], points[-1]["speed_mps"], profile["energy_kwh"]
    i = max(0, bisect_right(times, t) - 1)
    a, b = points[i : i + 2]
    dt, step = t - times[i], times[i + 1] - times[i]
    acceleration = (b["speed_mps"] - a["speed_mps"]) / step
    dx = a["speed_mps"] * dt + 0.5 * acceleration * dt * dt
    distance = b["position_m"] - a["position_m"]
    fraction = max(0.0, min(1.0, dx / distance)) if distance else 0.0
    energy = profile["_traction_kwh"][i] + fraction * (
        profile["_traction_kwh"][i + 1] - profile["_traction_kwh"][i]
    )
    energy += profile["_auxiliary_power_w"] * t / 3_600_000
    return a["position_m"] + dx, a["speed_mps"] + acceleration * dt, energy


def waiting_energy(train, stops, now):
    """Auxiliary power from release, including off-network and terminal waiting."""

    def elapsed(start, end):
        return max(0, min(now, end) - start)

    seconds = elapsed(train["release_s"], stops[0]["arrival_s"])
    seconds += sum(elapsed(s["arrival_s"], s["departure_s"]) for s in stops)
    return train["auxiliary_power_w"] * seconds / 3_600_000


def resource_states(state):
    """Use native half-open constraint/reservation intervals, including each track."""
    scenario, plan, now = state["scenario"], state["active_plan"], state["sim_time_s"]
    blocks = [b for b in scenario["blocks"] if b["start_s"] <= now < b["end_s"]]
    trains = {t["id"]: t for t in scenario["trains"]}
    sections = []
    for section in scenario["sections"]:
        occupied = [
            m
            for m in plan["movements"]
            if m["section_id"] == section["id"] and m["start_s"] <= now < m["release_s"]
        ]
        recovering = [
            m
            for m in occupied
            if now < m["start_s"] + m["hold_s"]
            and section["id"] in trains[m["train_id"]]["section_recovery_all_tracks"]
        ]
        limits = [s for s in section["entry_speed_limits"] if s["start_s"] <= now < s["end_s"]]
        factor = min((s["speed_factor"] for s in limits), default=1.0)
        tracks = []
        for track in section["main_tracks"]:
            resources = {
                f"section:{section['id']}",
                f"main_track:{section['id']}:{track['id']}",
                *section["shared_resources"],
            }
            active = [b for b in blocks if b["resource"] in resources]
            closure = any(b["kind"] == "closure" for b in active) or bool(recovering)
            signal = any(b["kind"] == "signal" for b in active)
            occupants = [m["train_id"] for m in occupied if m["main_track_id"] == track["id"]]
            status = (
                "closed"
                if closure
                else "signal_failure"
                if signal
                else "occupied"
                if occupants
                else "open"
            )
            tracks.append(
                dict(
                    track,
                    status=status,
                    occupying=occupants,
                    signal="red" if closure or signal or occupants else "green",
                    blocked_by=[b["id"] for b in active],
                    recovery_train_ids=[m["train_id"] for m in recovering],
                )
            )
        # Keep the existing frontend vocabulary; per-track details preserve partial closures.
        status = (
            "closed"
            if all(t["status"] == "closed" for t in tracks)
            else "signal_failure"
            if any(t["status"] in ("closed", "signal_failure") for t in tracks)
            else "occupied"
            if occupied
            else "open"
        )
        sections.append(
            {
                "id": section["id"],
                "status": status,
                "occupying": [m["train_id"] for m in occupied],
                "tracks": tracks,
                "signal": "red" if any(t["signal"] == "red" for t in tracks) else "green",
                "partial_closure": any(t["status"] == "closed" for t in tracks)
                and status != "closed",
                "entry_speed_factor": factor,
                "speed_limit_mps": section["max_speed_mps"],
                "speed_restrictions": limits,
            }
        )
    stations = []
    for station in scenario["stations"]:
        tracks = []
        for track in station["tracks"]:
            resource = f"track:{station['id']}:{track['id']}"
            active = [b for b in blocks if b["resource"] == resource]
            occupants = [
                s["train_id"]
                for s in plan["_native"]["stops"]
                if s["station_id"] == station["id"]
                and s["track_id"] == track["id"]
                and s["arrival_s"] <= now < s["departure_s"] + station["clearance_s"]
            ]
            tracks.append(
                dict(
                    track,
                    occupying=occupants,
                    blocked_by=[b["id"] for b in active],
                    status="closed" if active else "occupied" if occupants else "open",
                )
            )
        stations.append({"id": station["id"], "tracks": tracks})
    return sections, stations


def observed_conflicts(state):
    """Count reservation overlaps already reached, without scoring future conflicts."""
    if "execution" in state:
        return len(state["execution"]["conflicts"])
    now, plan = state["sim_time_s"], state["active_plan"]
    reservations = {}

    def reserve(resource, start, end, train_id):
        if start <= now:
            reservations.setdefault(resource, []).append((start, end, train_id))

    stations = {s["id"]: s for s in state["scenario"]["stations"]}
    sections = {s["id"]: s for s in state["scenario"]["sections"]}
    for stop in plan["_native"]["stops"]:
        reserve(
            f"track:{stop['station_id']}:{stop['track_id']}",
            stop["arrival_s"],
            stop["departure_s"] + stations[stop["station_id"]]["clearance_s"],
            stop["train_id"],
        )
    for move in plan["movements"]:
        for resource in (
            f"main_track:{move['section_id']}:{move['main_track_id']}",
            *sections[move["section_id"]]["shared_resources"],
        ):
            reserve(resource, move["start_s"], move["release_s"], move["train_id"])
    from .switches import reservations as switch_reservations
    for item in switch_reservations(state["scenario"], plan["_native"]):
        reserve(item["resource"], item["start_s"], item["end_s"], item["train_id"])
    count = 0
    for entries in reservations.values():
        for i, (start, end, _) in enumerate(entries):
            count += sum(
                start < peer_end and peer_start < end
                for peer_start, peer_end, _ in entries[i + 1 :]
            )
    return count


def actual_metrics(state, fleet, config, violations):
    now = state["sim_time_s"]
    weighted = sum(t["delay_s"] * t["priority"] for t in fleet)
    mean_weighted = weighted / sum(t["priority"] for t in fleet)
    cancelled_energy = state["scenario"].get("metadata", {}).get("cancelled_energy_kwh", 0.0)
    energy = sum(t["energy_kwh"] for t in fleet) + cancelled_energy
    arrived = [t for t in fleet if t["status"] == "completed"]
    on_time = (
        (
            sum(abs(t["arrival_s"] - t["due_s"]) <= config.arrival_tolerance_s for t in arrived)
            / len(arrived)
        )
        if arrived
        else None
    )
    window = min(now, state["scenario"]["evaluation_end_s"])
    eligible = [t for t in fleet if t["due_s"] <= window]
    completed = sum(t["arrival_s"] is not None and t["arrival_s"] <= window for t in eligible)
    conflicts = observed_conflicts(state)
    components = {
        "punctuality": max(0.0, 1 - mean_weighted / config.delay_scale_s),
        "throughput": completed / len(eligible) if eligible else 1.0,
        "energy": max(0.0, 1 - energy / config.energy_budget_kwh),
        "conflicts": 0.0 if conflicts else 1.0,
        "arrival_accuracy": on_time,
    }
    # Before the first arrival, accuracy is unknown; expose a provisional index.
    index = config.score(components)
    return {
        "kind": "synthetic_actual",
        "index": index,
        "category": config.category(index, not conflicts),
        "contributions": config.contributions(components),
        "components": components,
        "provisional": on_time is None,
        "config": config.model_dump(),
        "total_delay_s": sum(t["delay_s"] for t in fleet),
        "max_delay_s": max(t["delay_s"] for t in fleet),
        "weighted_delay_s": weighted,
        "passenger_delay_s": sum(t["delay_s"] for t in fleet if t["type"] == "passenger"),
        "energy_kwh": energy,
        "cancelled_energy_kwh": cancelled_energy,
        "completed_trips": len(arrived),
        "scheduled_trips": sum(t["due_s"] <= now for t in fleet),
        "completed_by_window": completed,
        "scheduled_by_window": len(eligible),
        "on_time_pct": None if on_time is None else on_time * 100,
        "conflicts": conflicts,
        "plan_violation_count": len(violations),
        "plan_applicable": not violations,
        "applicable": True,
    }
