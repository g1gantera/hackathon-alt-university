"""Signal aspects and occupancy use the same actual block ledger as movement."""

import math


def occupancy(sim):
    errors = []
    occupied = {}
    for tid, r in sim.footprints():
        m = r["move"]
        section = sim.sections[m["section_id"]]
        count = max(1, math.ceil(section.length_m / (section.block_length_m or section.length_m)))
        width = section.length_m / count
        rear = max(0.0, r["x"] - sim.trains[tid].length_m - 25)
        for order in range(count):
            if (order + 1) * width < rear or order * width > r["x"]:
                continue
            index = order if m["origin"] == section.station_a else count - 1 - order
            key = (section.id, m["main_track_id"], index)
            if key in occupied and occupied[key] != tid:
                errors.append(
                    dict(code="ACTUAL_BLOCK_CONFLICT", message=f"{key}: {occupied[key]} / {tid}")
                )
            occupied[key] = tid
        if r["x"] < -1e-5 or r["x"] > section.length_m + 1e-5 or r.get("v", 0) < 0:
            errors.append(dict(code="ACTUAL_POSITION", message=tid))
    station_claims = {}
    now = sim.state["sim_time_s"]
    for tid, r in sim.live.items():
        if not r["admitted"] or r["complete"] and now >= r["release"]:
            continue
        claims = []
        if r["moving"]:
            claims.append((r["move"]["destination"], r["arrival_track"]))
            if r["x"] <= sim.trains[tid].length_m + 25:
                claims.append((r["move"]["origin"], r["track"]))
        else:
            claims.append((sim.trains[tid].route[r["leg"]], r["track"]))
        for claim in claims:
            if claim in station_claims and station_claims[claim] != tid:
                errors.append(dict(code="ACTUAL_RECEIVING_CONFLICT", message=f"{claim}"))
            station_claims[claim] = tid
    return occupied, errors


def observations(sim, snapshot):
    occupied, errors = occupancy(sim)
    signals = [
        dict(
            signal,
            aspect="yellow" if signal.get("type") == "warning" else "red",
            reason="Нет действующего блочного разрешения",
        )
        for signal in snapshot["signals"]
        if signal.get("type") != "block"
    ]
    for item in snapshot["sections"]:
        section = sim.sections[item["id"]]
        count = max(1, math.ceil(section.length_m / (section.block_length_m or section.length_m)))
        width = section.length_m / count
        item["occupying"] = sorted(
            {owner for (sid, _, _), owner in occupied.items() if sid == section.id}
        )
        item["status"] = (
            "closed"
            if sim.blocked("section:" + section.id)
            else "occupied"
            if item["occupying"]
            else "open"
        )
        for track in item.get("tracks", []):
            owners = sorted(
                {
                    owner
                    for (sid, track_id, _), owner in occupied.items()
                    if sid == section.id and track_id == track["id"]
                }
            )
            closed = sim.blocked("section:" + section.id) or sim.blocked(
                f"main_track:{section.id}:{track['id']}"
            )
            track.update(
                occupying=owners,
                status="closed" if closed else "occupied" if owners else "open",
                signal="red" if closed or owners else "green",
            )
        for tid, r in sim.live.items():
            if not r["moving"] or r["move"]["section_id"] != section.id:
                continue
            m = r["move"]
            forward = m["origin"] == section.station_a
            for order in range(count):
                index = order if forward else count - 1 - order
                busy = occupied.get((section.id, m["main_track_id"], index))
                permitted = (
                    not busy
                    and r["x"] <= order * width < r["authority"]
                    and not sim.blocked("section:" + section.id)
                    and not sim.blocked(f"main_track:{section.id}:{m['main_track_id']}")
                )
                following = index + (1 if forward else -1)
                second = occupied.get((section.id, m["main_track_id"], following))
                aspect = (
                    "red"
                    if not permitted
                    else "yellow"
                    if second or r["authority"] - (order * width) < 2 * width
                    else "green"
                )
                signals.append(
                    dict(
                        id=f"actual:{section.id}:{m['main_track_id']}:{m['origin']}:{order}",
                        type="block",
                        station_id=m["origin"],
                        section_id=section.id,
                        main_track_id=m["main_track_id"],
                        offset_m=order * width,
                        aspect=aspect,
                        train_id=tid,
                        synthetic=True,
                        occupied_by=busy,
                        reason="Фактическая занятость и разрешение; положение светофора модельное",
                    )
                )
    for switch in snapshot["switches"]:
        sid = switch["station_id"]
        lock = sim.state["execution"]["throats"].get(sid)
        owners = [lock["owner"]] if lock else []
        blocked = sim.blocked(switch["id"])
        switch.update(
            available=not owners and not blocked,
            status="blocked" if blocked else "locked" if owners else "free",
            train_id=next(iter(owners), None),
        )
        if owners:
            r = sim.live[owners[0]]
            track = r["arrival_track"] if r["move"]["destination"] == sid else r["track"]
            switch["position"] = (
                "normal" if track == sim.stations[sid]["tracks"][0]["id"] else "reverse"
            )
    snapshot["signals"] = signals
    sim.state["execution"]["conflicts"] = errors
    if errors:
        sim.state["running"] = False
        snapshot["running"] = False
    return errors
