"""Synthetic throat route locks, independent of station dwell/main-line occupancy.

One lock per station is a conservative assumption, not an OSM switch inventory.
Arrival/departure times are front-of-train crossing instants; the following
clearance interval represents the whole route being occupied. Terminal admission
and removal outside the modeled line do not use its throat.
"""


def reservations(scenario, plan):
    stations = {s["id"]: s for s in scenario["stations"]}
    trains = {t["id"]: t for t in scenario["trains"]}
    result = []
    for stop in plan["stops"]:
        station = stations[stop["station_id"]]
        switch = station.get("switch")
        if not switch:
            continue
        route = trains[stop["train_id"]]["route"]
        index = route.index(station["id"])
        for kind, when, used in [
            ("arrival", stop["arrival_s"], index > 0),
            ("departure", stop["departure_s"], index < len(route) - 1),
        ]:
            if used:
                result.append(
                    dict(
                        resource=f"switch:{station['id']}:{switch['id']}",
                        station_id=station["id"],
                        train_id=stop["train_id"],
                        track_id=stop["track_id"],
                        kind=kind,
                        start_s=when,
                        end_s=when + switch["clearance_s"],
                        position="normal"
                        if stop["track_id"] == station["tracks"][0]["id"]
                        else "reverse",
                    )
                )
    return result


def states(state):
    now, scenario = state["sim_time_s"], state["scenario"]
    locks = reservations(scenario, state["active_plan"]["_native"])
    result = []
    for station in scenario["stations"]:
        switch = station.get("switch")
        if not switch:
            continue
        resource = f"switch:{station['id']}:{switch['id']}"
        routes = [r for r in locks if r["resource"] == resource]
        active = [r for r in routes if r["start_s"] <= now < r["end_s"]]
        past = sorted((r for r in routes if r["start_s"] <= now), key=lambda r: r["start_s"])
        blocks = [
            b["id"]
            for b in scenario["blocks"]
            if b["resource"] == resource and b["start_s"] <= now < b["end_s"]
        ]
        result.append(
            dict(
                id=resource,
                station_id=station["id"],
                position=past[-1]["position"] if past else "normal",
                available=not active and not blocks,
                train_id=active[0]["train_id"] if active else None,
                routes=active,
                blocked_by=blocks,
                status="blocked" if blocks else "locked" if active else "free",
                synthetic=True,
                clearance_s=switch["clearance_s"],
            )
        )
    return result
