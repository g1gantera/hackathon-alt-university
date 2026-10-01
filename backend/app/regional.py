"""One station/resource namespace for all regional train routes."""

import heapq
import json
from pathlib import Path

from .scenarios import corridor_scenario
from .schemas import MainTrack, Scenario, Section, Station, Track
from .station_capacity import expanded_stations

PATH = Path(__file__).resolve().parents[2] / "data/region/infrastructure.json"


def infrastructure():
    return json.loads(PATH.read_text())


def route_between(scenario, origin, destination):
    if origin == destination:
        raise ValueError("Начальная и конечная станции должны различаться")
    graph = {}
    for s in scenario.sections:
        if any(t.direction in ("both", "a_to_b") for t in s.main_tracks):
            graph.setdefault(s.station_a, []).append((s.station_b, s.length_m))
        if any(t.direction in ("both", "b_to_a") for t in s.main_tracks):
            graph.setdefault(s.station_b, []).append((s.station_a, s.length_m))
    costs = {origin: 0}
    queue = [(0, origin)]
    parent = {}
    while queue:
        d, u = heapq.heappop(queue)
        if d != costs[u]:
            continue
        if u == destination:
            break
        for v, w in graph.get(u, []):
            if d + w < costs.get(v, float("inf")):
                costs[v] = d + w
                parent[v] = u
                heapq.heappush(queue, (d + w, v))
    if destination not in parent:
        raise ValueError("Нет связного маршрута в исходной карте; синтетический мост не создаётся")
    path = [destination]
    while path[-1] != origin:
        path.append(parent[path[-1]])
    return path[::-1]


def regional_scenario(service_date="2026-10-02", multiplier=1):
    infra = infrastructure()
    base = corridor_scenario()
    stations = [
        Station(
            id=s["id"],
            name=s["name"],
            tracks=[Track(id="SIM-1", length_m=1500), Track(id="SIM-2", length_m=1500)],
            clearance_s=120,
            switch={"id": "throat", "clearance_s": 15},
        )
        for s in infra["stations"]
    ]
    # A shared OSM edge is locked even when abstract station links partly overlap.
    from collections import Counter

    uses = Counter(e for s in infra["sections"] for e in s["edge_ids"])
    sections = [
        Section(
            id=s["id"],
            station_a=s["station_a"],
            station_b=s["station_b"],
            length_m=s["length_m"],
            max_speed_mps=80 / 3.6,
            block_length_m=1500,
            main_tracks=[MainTrack(id="1")],
            shared_resources=[f"junction:osm_edge_{e}" for e in s["edge_ids"] if uses[e] > 1],
        )
        for s in infra["sections"]
    ]
    metadata = {
        "corridor_key": "akmola_network",
        "network": True,
        "mapping_evidence": {
            f"station:{s['id']}": {
                "osm_node_id": s["osm_node_id"],
                "status": "assumed_for_simulation",
            }
            for s in infra["stations"]
        },
        "assumptions": [
            "Граница области из открытого набора за 2017 год; пограничные подходы включены отдельно.",
            "OSM не подтверждает эксплуатационные пути и расписание.",
            "Общие рёбра OSM и горловины резервируются совместно для всех поездов.",
        ],
        "coverage": infra["coverage"],
    }
    # Construct a validated minimal seed, then append independent branch routes.
    first = sections[0]
    template = base.trains[0].model_copy(deep=True)
    template.route = [first.station_a, first.station_b]
    scenario = Scenario(
        id="akmola-network",
        stations=stations,
        sections=sections,
        trains=[template],
        horizon_s=7 * 86400,
        evaluation_end_s=86400,
        metadata=metadata,
    )
    scenario = expanded_stations(scenario)
    pairs = [
        ("4020137343", "4026381503"),
        ("4020137343", "4020137344"),
        ("4020137343", "4025083260"),
        ("4020137344", "9037950278"),
        ("9037950278", "4035828316"),
        ("4026381503", "1543866992"),
        ("5810748819", "1985886766"),
        ("1985886766", "1857792773"),
        ("4020137343", "3191975962"),
    ]
    trains = []
    unavailable = []
    for replica in range(multiplier):
        for i, (a, b) in enumerate(pairs):
            try:
                route = route_between(scenario, "OSM_" + a, "OSM_" + b)
            except ValueError as e:
                unavailable.append({"from": a, "to": b, "reason": str(e)})
                continue
            for reverse in (False, True):
                t = next(
                    t for t in base.trains if t.kind == ("freight" if i % 3 == 2 else "passenger")
                ).model_copy(deep=True)
                t.id = f"NET-{replica + 1}-{i + 1:02}-{int(reverse)}"
                t.route = route[::-1] if reverse else route[:]
                t.release_s = i * 2400 + int(reverse) * 600 + replica * 300
                t.due_s = 7 * 86400
                from .advisory.speed import minimum_duration_s
                from .planning.common import section_for

                t.due_s = (
                    t.release_s
                    + sum(
                        minimum_duration_s(t, section_for(scenario, a, b), a)
                        for a, b in zip(t.route, t.route[1:])
                    )
                    + len(t.route) * t.min_dwell_s
                    + 600
                )
                t.dispatch_category = t.kind
                t.priority = 70 if t.kind == "passenger" else 20
                trains.append(t)
    scenario.trains = trains
    scenario.metadata["traffic"] = {
        "profile": "demo",
        "service_date": service_date,
        "train_count": len(trains),
        "source": "synthetic",
        "note": "Совместная нагрузка веток синтетическая. Это не полный суточный график КТЖ.",
        "unavailable_routes": unavailable,
    }
    return Scenario.model_validate(scenario.model_dump())


def regional_topology(scenario):
    from .track_display import display_tracks

    infra = infrastructure()
    stations = [
        {
            "id": s["id"],
            "name": s["name"],
            "position_m": s["distance_m"],
            "coordinate": s["coordinate"],
            "tracks": len(scenario.stations[i].tracks),
            "regional": s["regional"],
        }
        for i, s in enumerate(infra["stations"])
    ]
    sections = [
        {
            "id": s.id,
            "from_station": s.station_a,
            "to_station": s.station_b,
            "length_m": s.length_m,
            "speed_limit_mps": s.max_speed_mps,
            "geometry": raw["geometry"],
            "main_tracks": [t.model_dump() for t in s.main_tracks],
        }
        for s, raw in zip(scenario.sections, infra["sections"])
    ]
    display_tracks(stations, sections, scenario, infra, [])
    return {
        "corridor_id": "akmola_network",
        "network": True,
        "coverage": infra["coverage"],
        "name": infra["name"],
        "stations": stations,
        "sections": sections,
        "length_m": infra["length_m"],
        "assumptions": scenario.metadata["assumptions"],
        "engine": "logic",
    }
