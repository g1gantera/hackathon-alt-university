import math

from backend.app.schemas import Plan, Scenario, Section, Train


def section_for(scenario: Scenario, origin: str, destination: str) -> Section:
    return next(s for s in scenario.sections if {s.station_a, s.station_b} == {origin, destination})


def clearance_s(train: Train, section: Section) -> int:
    return section.headway_s + math.ceil(train.length_m / section.tail_clearance_speed_mps)


def allowed_main_tracks(section: Section, origin: str, train: Train | None = None):
    direction = "a_to_b" if origin == section.station_a else "b_to_a"
    return [track for track in section.main_tracks if track.direction in ("both", direction)
            and (train is None or section.id not in train.manual_main_tracks or train.manual_main_tracks[section.id] == track.id)]


def committed_fields(scenario: Scenario, previous: Plan | None):
    """Lock past arrivals/departures plus movement already entered and its target track."""
    arrivals, departures, tracks, movements = {}, {}, {}, {}
    if previous is None:
        return arrivals, departures, tracks, movements
    for stop in previous.stops:
        key = (stop.train_id, stop.station_id)
        if stop.arrival_s <= scenario.now_s:
            arrivals[key] = stop.arrival_s
            tracks[key] = stop.track_id
        if stop.departure_s <= scenario.now_s:
            departures[key] = stop.departure_s
    stop_map = {(s.train_id, s.station_id): s for s in previous.stops}
    for move in previous.movements:
        if move.start_s <= scenario.now_s:
            key = (move.train_id, move.origin)
            movements[key] = (move.start_s, move.end_s, move.main_track_id)
            destination = (move.train_id, move.destination)
            arrivals[destination] = move.end_s
            tracks[destination] = stop_map[destination].track_id
    return arrivals, departures, tracks, movements


def merged_closures(scenario: Scenario):
    resources: dict[str, list[tuple[int, int]]] = {}
    sections = {s.id: s for s in scenario.sections}
    for block in scenario.blocks:
        if block.kind == "closure":
            targets = [block.resource]
            if block.resource.startswith("section:"):
                section = sections[block.resource.removeprefix("section:")]
                targets = [f"main_track:{section.id}:{t.id}" for t in section.main_tracks]
            for resource in targets:
                resources.setdefault(resource, []).append((block.start_s, block.end_s))
    for resource, intervals in resources.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        resources[resource] = merged
    return resources


def wear_cost(scenario, resource):
    return round(scenario.metadata.get("track_wear", {}).get(resource, {}).get("wear_pct", 50))
