"""Station graph from OSM rail edges. No invented bridges across disconnected components.

Voronoi territories identify neighbouring stations. Each link retains original
edges; shared edges become exclusive resources in the operating model.
"""

import heapq
import json
from pathlib import Path

from build_akmola import distance

ROOT = Path(__file__).resolve().parents[1]


def inside_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def inside(point, geometry):
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return any(
        inside_ring(point, p[0]) and not any(inside_ring(point, h) for h in p[1:]) for p in polys
    )


def main():
    n = json.loads((ROOT / "network.json").read_text())
    boundary = json.loads((ROOT / "data/region/boundary.geojson").read_text())
    vertices = {v["id"]: [v["lon"], v["lat"]] for v in n["vertices"]}
    graph = {}
    edges = {}
    for e in n["edges"]:
        way = n["ways"][e["way"]]
        if way["synthetic"] or e["category"] not in (
            "main",
            "branch",
            "line",
            "siding",
            "crossover",
            "industrial",
            "yard",
            "spur",
        ):
            continue
        if not all(
            65 < vertices[v][0] < 75 and 50.2 < vertices[v][1] < 54.2 for v in (e["u"], e["v"])
        ):
            continue
        edges[e["id"]] = e
        for u, v in ((e["u"], e["v"]), (e["v"], e["u"])):
            graph.setdefault(u, []).append((v, e["id"], e["length_m"]*(10 if e["category"] in ("industrial","yard","spur") else 1)))
    stations = {}
    skipped = []
    for s in n["stations"]:
        if s["type"] != "station" or not (65 < s["lon"] < 75 and 50.2 < s["lat"] < 54.2):
            continue
        regional = any(inside([s["lon"], s["lat"]], f["geometry"]) for f in boundary["features"])
        vertex = s["vertex"]
        if vertex not in graph or s["snap_dist_m"] > 1000:
            if regional:
                skipped.append(
                    {
                        "osm_id": s["osm_id"],
                        "name": s["name"],
                        "reason": "No heavy-rail graph attachment within 1 km; may be LRT or disconnected",
                    }
                )
            continue
        # Multiple OSM station labels can refer to one mapped anchor.
        if vertex in stations:
            skipped.append(
                {"osm_id": s["osm_id"], "name": s["name"], "reason": "Duplicate graph anchor"}
            )
            continue
        stations[vertex] = dict(
            id="OSM_" + str(s["osm_id"]),
            name=s["name"] or f"Станция {s['osm_id']}",
            osm_node_id=str(s["osm_id"]),
            coordinate=vertices[vertex],
            distance_m=len(stations) * 100000.0,
            model_track_count=2,
            model_track_length_m=1500,
            station_capacity_status="assumed_for_simulation",
            mapped_track_observation={"intersections": [], "mapped_track_count": None},
            regional=regional,
        )
    dist = {v: 0.0 for v in stations}
    owner = {v: v for v in stations}
    parent = {}
    queue = [(0.0, v) for v in stations]
    heapq.heapify(queue)
    while queue:
        d, u = heapq.heappop(queue)
        if d != dist[u]:
            continue
        for v, e, w in graph[u]:
            if d + w < dist.get(v, float("inf")):
                dist[v] = d + w
                owner[v] = owner[u]
                parent[v] = (u, e)
                heapq.heappush(queue, (d + w, v))
    neighbors = {}
    for e in edges.values():
        u, v = e["u"], e["v"]
        if u not in owner or v not in owner or owner[u] == owner[v]:
            continue
        pair = tuple(sorted((owner[u], owner[v])))
        candidate = (dist[u] + e["length_m"] + dist[v], u, v, e["id"])
        if pair not in neighbors or candidate < neighbors[pair]:
            neighbors[pair] = candidate
    sections = []
    for _, (_, u, v, eid) in sorted(neighbors.items()):
        left = []
        q = u
        while q in parent:
            p, e = parent[q]
            left.append((p, q, e))
            q = p
        a = q
        path = left[::-1] + [(u, v, eid)]
        q = v
        while q in parent:
            p, e = parent[q]
            path.append((q, p, e))
            q = p
        b = q
        coords = []
        for x, y, e in path:
            pts = edges[e]["geometry"]
            if distance(pts[0], vertices[x]) > distance(pts[-1], vertices[x]):
                pts = pts[::-1]
            coords.extend(pts if not coords else pts[1:])
        sa, sb = stations[a]["id"], stations[b]["id"]
        sections.append(
            dict(
                id=sa + "__" + sb,
                station_a=sa,
                station_b=sb,
                length_m=sum(distance(x, y) for x, y in zip(coords, coords[1:])),
                model_main_track_count=1,
                status="assumed_for_simulation",
                mapped_main_track_count=None,
                osm_way_ids=sorted({n["ways"][edges[e]["way"]]["osm_id"] for _, _, e in path}),
                edge_ids=[e for _, _, e in path],
                geometry=coords,
            )
        )
    # Preserve observed labels/lengths separately from usable station capacity.
    buckets = {}
    for edge in n["edges"]:
        way = n["ways"][edge["way"]]
        if way["synthetic"] or way["tags"].get("railway") != "rail":
            continue
        for point in edge["geometry"]:
            if 65 < point[0] < 75 and 50.2 < point[1] < 54.2:
                buckets.setdefault((int(point[0] * 20), int(point[1] * 20)), set()).add(edge["id"])
    all_edges = {e["id"]: e for e in n["edges"]}
    for station in stations.values():
        point = station["coordinate"]
        bx, by = int(point[0] * 20), int(point[1] * 20)
        candidates = set().union(
            *(buckets.get((bx + dx, by + dy), set()) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
        )
        observed = {}
        for eid in candidates:
            e = all_edges[eid]
            way = n["ways"][e["way"]]
            if min(distance(point, p) for p in e["geometry"]) > 500:
                continue
            item = observed.setdefault(
                way["osm_id"],
                {
                    "osm_way_id": way["osm_id"],
                    "reference_tag": way["tags"].get("ref"),
                    "service": way["tags"].get("service"),
                    "usage": way["tags"].get("usage"),
                    "maxspeed_tag": way["tags"].get("maxspeed"),
                    "geometry_length_within_500m_m": 0,
                },
            )
            item["geometry_length_within_500m_m"] += sum(
                distance(a, b)
                for a, b in zip(e["geometry"], e["geometry"][1:])
                if distance(point, a) <= 500 and distance(point, b) <= 500
            )
        station["observed_ways"] = list(observed.values())
        station["inventory_note"] = (
            "OSM way fragments within 500 m. Not usable track lengths, official numbering, or an operating turnout diagram."
        )
    adjacency = {s["id"]: set() for s in stations.values()}
    for section in sections:
        a, b = section["station_a"], section["station_b"]
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen = set()
    components = []
    for sid in adjacency:
        if sid in seen:
            continue
        todo = [sid]
        seen.add(sid)
        component = []
        while todo:
            s = todo.pop()
            component.append(s)
            for peer in adjacency[s] - seen:
                seen.add(peer)
                todo.append(peer)
        components.append(component)
    data = dict(
        id="akmola_network",
        name="Единая сеть Акмолы и пограничные подходы",
        stations=list(stations.values()),
        sections=sections,
        length_m=sum(s["length_m"] for s in sections),
        operational_exactness=False,
        source={
            "file": "network.json",
            "osm_timestamp": n["stats"]["osm_timestamp"],
            "attribution": n["attribution"],
            "synthetic_edges_used": False,
            "boundary_year": 2017,
        },
        coverage={
            "station_count": len(stations),
            "regional_station_count": sum(s["regional"] for s in stations.values()),
            "reachable_directed_pairs": sum(len(c) * (len(c) - 1) for c in components),
            "components": components,
            "skipped": skipped,
            "note": "All connected station pairs in this extracted graph, not a certified list of operating routes. Regional membership uses archived 2017 boundary; outer stations are boundary approaches.",
        },
    )
    (ROOT / "data/region/infrastructure.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        len(stations),
        "stations",
        sum(s["regional"] for s in stations.values()),
        "regional;",
        len(sections),
        "links;",
        len(skipped),
        "excluded",
    )


if __name__ == "__main__":
    main()
