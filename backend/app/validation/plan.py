"""Independent structural, physical and interval checks for any proposed plan."""

import math
from collections import Counter

from backend.app.advisory.speed import minimum_duration_s, section_at_entry
from backend.app.schemas import Plan, Scenario, Violation


def validate_plan(scenario: Scenario, plan: Plan, previous: Plan | None = None) -> list[Violation]:
    errors = []

    def add(code, message, trains=(), resource=None):
        errors.append(
            Violation(code=code, message=message, train_ids=list(trains), resource=resource)
        )

    if plan.scenario_id != scenario.id or plan.state_version != scenario.state_version:
        add("STALE_PLAN", "Plan belongs to another scenario or state version")
    if scenario.now_s > 0 and previous is None:
        add("MISSING_PREVIOUS", "A previous plan is required to preserve started movements")
    if previous and (
        previous.scenario_id != scenario.id or previous.state_version > scenario.state_version
    ):
        add("INVALID_PREVIOUS", "Previous plan belongs to another scenario or future state")
        return errors
    trains = {t.id: t for t in scenario.trains}
    stations = {s.id: s for s in scenario.stations}
    sections = {s.id: s for s in scenario.sections}
    stops = {(s.train_id, s.station_id): s for s in plan.stops}
    moves = {(m.train_id, m.origin): m for m in plan.movements}
    expected_stops = {(t.id, s) for t in scenario.trains for s in t.route}
    expected_moves = {(t.id, s) for t in scenario.trains for s in t.route[:-1]}
    if set(stops) != expected_stops or len(stops) != len(plan.stops):
        add("STOP_SET", "Stops are missing, duplicated or unexpected")
    if set(moves) != expected_moves or len(moves) != len(plan.movements):
        add("MOVEMENT_SET", "Movements are missing, duplicated or unexpected")
    if errors:
        return errors
    from backend.app.resource_roster import validate_roster
    errors.extend(validate_roster(scenario, plan))
    reservations = []  # (resource, start, end, train_id)
    for stop in plan.stops:
        train, station = trains[stop.train_id], stations[stop.station_id]
        track = next((t for t in station.tracks if t.id == stop.track_id), None)
        if track is None or track.length_m < train.length_m:
            add("TRACK", "Unknown track or insufficient track length", [train.id])
        if stop.station_id in train.manual_station_tracks and stop.track_id != train.manual_station_tracks[stop.station_id]:
            add("MANUAL_TRACK", "Station track differs from dispatcher assignment", [train.id])
        if stop.departure_s < stop.arrival_s + train.min_dwell_s:
            add("DWELL", "Minimum station dwell is violated", [train.id])
        if stop.departure_s < train.not_before_s.get(stop.station_id, 0):
            add("TRAIN_DELAY", "Departure precedes the imposed train delay", [train.id])
        if stop.departure_s + station.clearance_s > scenario.horizon_s:
            add("HORIZON", "Track release exceeds planning horizon", [train.id])
        if stop.station_id == train.route[0] and stop.arrival_s < train.release_s:
            add("RELEASE", "Train is admitted before release time", [train.id])
        reservations.append(
            (
                f"track:{station.id}:{stop.track_id}",
                stop.arrival_s,
                stop.departure_s + station.clearance_s,
                train.id,
            )
        )
    from backend.app.switches import reservations as switch_reservations
    for reservation in switch_reservations(scenario.model_dump(), plan.model_dump()):
        reservations.append((reservation["resource"], reservation["start_s"],
                             reservation["end_s"], reservation["train_id"]))
        if reservation["end_s"] > scenario.horizon_s:
            add("HORIZON", "Switch release exceeds planning horizon", [reservation["train_id"]])
    for train in scenario.trains:
        for origin, destination in zip(train.route, train.route[1:]):
            move = moves[(train.id, origin)]
            section = sections.get(move.section_id)
            if section is None or {section.station_a, section.station_b} != {origin, destination}:
                add("ROUTE", "Movement uses a section outside its route", [train.id])
                continue
            if move.destination != destination:
                add("ROUTE", "Wrong movement destination", [train.id])
                continue
            main_track = next((t for t in section.main_tracks if t.id == move.main_track_id), None)
            resource = f"main_track:{section.id}:{move.main_track_id}"
            if main_track is None:
                add("MAIN_TRACK", "Movement uses an unknown main track", [train.id], resource)
            else:
                direction = "a_to_b" if origin == section.station_a else "b_to_a"
                if main_track.direction not in ("both", direction):
                    add(
                        "DIRECTION",
                        "Main track does not permit this direction",
                        [train.id],
                        resource,
                    )
            if (
                move.start_s != stops[(train.id, origin)].departure_s
                or move.end_s != stops[(train.id, destination)].arrival_s
            ):
                add("CONTINUITY", "Movement and station times disagree", [train.id])
            if section.id in train.manual_main_tracks and move.main_track_id != train.manual_main_tracks[section.id]:
                add("MANUAL_TRACK", "Main track differs from dispatcher assignment", [train.id])
            if move.hold_s != train.section_hold_s.get(section.id, 0):
                add(
                    "SECTION_HOLD", "Movement does not preserve the known recovery hold", [train.id]
                )
            if move.end_s - move.start_s - move.hold_s < minimum_duration_s(
                train, section_at_entry(train, section, move.start_s), origin
            ):
                add("PHYSICS", "Movement is faster than physically feasible", [train.id])
            end = (
                move.end_s
                + section.headway_s
                + math.ceil(train.length_m / section.tail_clearance_speed_mps)
            )
            if end > scenario.horizon_s:
                add("HORIZON", "Section release exceeds planning horizon", [train.id])
            if section.block_length_m:
                from backend.app.block_sections import reservations as block_reservations
                try:
                    reservations.extend(block_reservations(train,section,move))
                except ValueError as error:
                    add("BLOCK_TIMING",str(error),[train.id],resource)
                for peer in plan.movements:
                    if peer.train_id!=train.id and peer.section_id==section.id and peer.main_track_id==move.main_track_id and peer.origin!=move.origin and move.start_s < peer.end_s + section.headway_s + math.ceil(trains[peer.train_id].length_m/section.tail_clearance_speed_mps) and peer.start_s < end:
                        add("OPPOSING", "Opposing routes overlap on a bidirectional line", [train.id,peer.train_id],resource)
                for block in scenario.blocks:
                    if block.kind=="closure" and block.resource==resource and move.start_s<block.end_s and block.start_s<end:
                        add("CLOSURE", "Movement overlaps main track closure",[train.id],resource)
            else:
                reservations.append((resource, move.start_s, end, train.id))
            reservations.extend((r, move.start_s, end, train.id) for r in section.shared_resources)
            for block in scenario.blocks:
                if (
                    block.kind == "signal"
                    and block.resource in (resource, f"section:{section.id}")
                    and block.start_s <= move.start_s < block.end_s
                ):
                    add("SIGNAL", "Entry under a prohibiting signal", [train.id], resource)
                if (
                    block.kind == "closure"
                    and block.resource == f"section:{section.id}"
                    and move.start_s < block.end_s
                    and block.start_s < end
                ):
                    add("CLOSURE", f"Reservation overlaps closure {block.id}", [train.id], resource)
    for resource in sorted({r[0] for r in reservations}):
        items = sorted(r for r in reservations if r[0] == resource)
        for i, (_, start, end, train_id) in enumerate(items):
            for _, next_start, next_end, next_train in items[i + 1 :]:
                if next_start >= end:
                    break
                if start < next_end and next_start < end:
                    add(
                        "RESOURCE_CONFLICT",
                        f"Overlapping reservations on {resource}",
                        [train_id, next_train],
                        resource,
                    )
            for block in scenario.blocks:
                if (
                    block.kind == "closure"
                    and block.resource == resource
                    and start < block.end_s
                    and block.start_s < end
                ):
                    add("CLOSURE", f"Reservation overlaps closure {block.id}", [train_id], resource)
    for move in plan.movements:
        if move.section_id not in trains[move.train_id].section_recovery_all_tracks:
            continue
        for peer in plan.movements:
            if peer.train_id == move.train_id or peer.section_id != move.section_id:
                continue
            section = sections[move.section_id]
            release = (
                peer.end_s
                + section.headway_s
                + math.ceil(trains[peer.train_id].length_m / section.tail_clearance_speed_mps)
            )
            if peer.start_s < move.start_s + move.hold_s and move.start_s < release:
                add(
                    "RECOVERY_CONFLICT",
                    "A whole-section recovery overlaps another train",
                    [move.train_id, peer.train_id],
                    f"section:{move.section_id}",
                )
    old_stops = {} if previous is None else {(s.train_id, s.station_id): s for s in previous.stops}
    old_moves = {} if previous is None else {(m.train_id, m.origin): m for m in previous.movements}
    for key, stop in stops.items():
        old = old_stops.get(key)
        incoming = next(
            (
                m
                for m in old_moves.values()
                if m.train_id == stop.train_id
                and m.destination == stop.station_id
                and m.start_s <= scenario.now_s
            ),
            None,
        )
        committed_arrival = old and (old.arrival_s <= scenario.now_s or incoming)
        if committed_arrival:
            if stop.arrival_s != old.arrival_s or stop.track_id != old.track_id:
                add("COMMITTED", "An arrived/approaching train changed arrival or track", [key[0]])
        elif stop.arrival_s < scenario.now_s:
            add("PAST", "New arrival was scheduled in the past", [key[0]])
        if old and old.departure_s <= scenario.now_s:
            if stop.departure_s != old.departure_s:
                add("COMMITTED", "A completed departure changed", [key[0]])
        elif stop.departure_s < scenario.now_s:
            add("PAST", "New departure was scheduled in the past", [key[0]])
    for key, old in old_moves.items():
        if old.start_s <= scenario.now_s:
            new = moves.get(key)
            if new != old:
                add("COMMITTED", "A started movement changed", [old.train_id])
    return errors


def summarize_violations(violations: list[Violation]) -> dict[str, int]:
    return dict(Counter(v.code for v in violations))
