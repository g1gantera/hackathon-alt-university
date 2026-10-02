"""Offline corridors from original OSM graph; excludes healed synthetic edges."""

import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def distance(a, b):
    x = math.radians(b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    y = math.radians(b[1] - a[1])
    return 6371000 * math.hypot(x, y)


def main():
    n = json.loads((ROOT / "network.json").read_text())
    graph = {}
    vertices = {v["id"]: [v["lon"], v["lat"]] for v in n["vertices"]}
    for e in n["edges"]:
        if n["ways"][e["way"]]["synthetic"] or e["category"] not in (
            "main",
            "branch",
            "line",
            "crossover",
            "siding",
        ):
            continue
        if not all(
            66 < vertices[v][0] < 74 and 50.8 < vertices[v][1] < 53.7 for v in (e["u"], e["v"])
        ):
            continue
        graph.setdefault(e["u"], []).append((e["v"], e))
        graph.setdefault(e["v"], []).append((e["u"], e))
    routes = [
        ("astana1_ereymentau", 4020137343, 4025083260),
        ("astana_atbasar", 13676555260, 4020137344),
        ("astana_ereymentau", 13676555260, 4025083260),
        ("atbasar_esil", 4020137344, 9037950278),
        ("astana_burabay", 13676555260, 5810748819),
        ("kokshetau_burabay", 4026381503, 5810748819),
    ]
    for ident, source_osm, osm in routes:
        source = next(s for s in n["stations"] if s["osm_id"] == source_osm)
        target = next(s for s in n["stations"] if s["osm_id"] == osm)

        def snap(s):
            return min(graph, key=lambda v: distance(vertices[v], [s["lon"], s["lat"]]))

        a, b = snap(source), snap(target)
        cost = {a: 0}
        parent = {}
        queue = [(0, a)]
        while queue:
            d, u = heapq.heappop(queue)
            if d != cost[u]:
                continue
            if u == b:
                break
            for v, e in graph[u]:
                w = d + e["length_m"] * (2 if e["category"] == "siding" else 1)
                if w < cost.get(v, float("inf")):
                    cost[v] = w
                    parent[v] = (u, e)
                    heapq.heappush(queue, (w, v))
        if b not in parent:
            raise RuntimeError("No connected OSM route " + ident)
        path = []
        v = b
        while v != a:
            u, e = parent[v]
            path.append((u, v, e))
            v = u
        path.reverse()
        points = []
        edge_ids = []
        edge_spans = []
        path_length = 0.0
        for u, v, e in path:
            coords = (
                e["geometry"]
                if distance(e["geometry"][0], vertices[u])
                < distance(e["geometry"][-1], vertices[u])
                else e["geometry"][::-1]
            )
            points.extend(coords if not points else coords[1:])
            edge_ids.append(e["id"])
            end = path_length + sum(distance(x, y) for x, y in zip(coords, coords[1:]))
            edge_spans.append((path_length, end, n["ways"][e["way"]]["osm_id"]))
            path_length = end
        lengths = [0]
        for x, y in zip(points, points[1:]):
            lengths.append(lengths[-1] + distance(x, y))
        candidates = []
        for station in n["stations"]:
            if station["type"] != "station":
                continue
            point = [station["lon"], station["lat"]]
            best = None
            for i, (x, y) in enumerate(zip(points, points[1:])):
                scale = math.cos(math.radians(point[1]))
                dx = (y[0] - x[0]) * scale
                dy = y[1] - x[1]
                f = max(
                    0,
                    min(
                        1,
                        ((point[0] - x[0]) * scale * dx + (point[1] - x[1]) * dy)
                        / (dx * dx + dy * dy or 1),
                    ),
                )
                q = [x[0] + f * (y[0] - x[0]), x[1] + f * (y[1] - x[1])]
                off = distance(point, q)
                value = (off, lengths[i] + f * (lengths[i + 1] - lengths[i]), q)
                if best is None or value[0] < best[0]:
                    best = value
            if best[0] < 450 or station in (source, target):
                candidates.append((best[1], station, best[2], best[0]))
        candidates = [
            c for c in candidates if c[1] in (source, target) or 1500 < c[0] < lengths[-1] - 1500
        ]
        candidates.sort(key=lambda x: x[0])
        stations = []
        for dist, s, point, off in candidates:
            if stations and dist - stations[-1]["distance_m"] < 1500 and s not in (source, target):
                continue
            sid = (
                "NURLY_ZHOL"
                if s["osm_id"] == 13676555260
                else "ASTANA_1"
                if s["osm_id"] == 4020137343
                else "OSM_" + str(s["osm_id"])
            )
            stations.append(
                dict(
                    id=sid,
                    name=s["name"] or f"Станция OSM {s['osm_id']}",
                    osm_node_id=str(s["osm_id"]),
                    distance_m=dist,
                    coordinate=point,
                    projection_offset_m=round(off, 2),
                    model_track_count=2,
                    model_track_length_m=1500,
                    station_capacity_status="assumed_for_simulation",
                    mapped_track_observation={"intersections": [], "mapped_track_count": None},
                )
            )
        stations[0]["distance_m"] = 0
        stations[-1]["distance_m"] = lengths[-1]
        sections = [
            dict(
                id=x["id"] + "__" + y["id"],
                station_a=x["id"],
                station_b=y["id"],
                length_m=y["distance_m"] - x["distance_m"],
                model_main_track_count=1,
                status="assumed_for_simulation",
                osm_way_ids=sorted(
                    {way for a, b, way in edge_spans if a < y["distance_m"] and b > x["distance_m"]}
                ),
                mapped_main_track_count=None,
            )
            for x, y in zip(stations, stations[1:])
        ]
        data = dict(
            id=ident,
            name=source["name"] + " ↔ " + target["name"],
            terminal_ids=[stations[0]["id"], stations[-1]["id"]],
            length_m=lengths[-1],
            stations=stations,
            sections=sections,
            geometry=points,
            operational_exactness=False,
            source=dict(
                file="network.json",
                osm_timestamp=n["stats"]["osm_timestamp"],
                edge_ids=edge_ids,
                synthetic_edges_used=False,
                attribution=n["attribution"],
            ),
            limitations=[
                "Один двусторонний главный путь — консервативное допущение; эксплуатационное число путей не подтверждено.",
                "Станционные пути и сигналы модельные; нагрузка синтетическая.",
            ],
        )
        folder = ROOT / "data/akmola"
        folder.mkdir(exist_ok=True)
        (folder / (ident + ".json")).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            ident,
            round(lengths[-1] / 1000, 1),
            [(s["name"], round(s["distance_m"] / 1000)) for s in stations],
        )


if __name__ == "__main__":
    main()
