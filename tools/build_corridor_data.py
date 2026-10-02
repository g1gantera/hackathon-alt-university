#!/usr/bin/env python3
"""Rebuild the corridor offline, or bootstrap its reproducible OSM snapshot.

Default input is the filtered snapshot embedded in data/corridor/sources.json.
To refresh it, pass --extracts FILE... and --routes ROUTE_JSON ROUTE_JSON.
This reads downloaded data only; it makes no network requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIONS = [
    ("KOKSHETAU_1", "Кокшетау-1", "4026381503"),
    ("RAZYEZD_17", "Разъезд № 17", "3805455712"),
    ("ZHAMANTUZ", "Джемантуз", "3805457185"),
    ("BURABAY", "Курорт Бурабай (Курорт Боровое)", "5810748819"),
    ("ZHASYL", "Жасыл", "4041217725"),
    ("MAKINKA", "Макинка", "4209174690"),
    ("ELTAY", "Ельтай", "4025083259"),
    ("AKKOL", "Акколь", "3191477785"),
    ("SHORTANDY", "Шортанды", "3207666940"),
    ("TONKERIS", "Тонкерис", "2629466566"),
    ("RAZYEZD_39", "Разъезд № 39 (Өндіріс)", "3805439410"),
    ("ASTANA_1", "Астана-1", "4020137343"),
    ("NURLY_ZHOL", "Астана Нурлы Жол", "13676555260"),
]
TAG_KEYS = {
    "name",
    "name:ru",
    "name:kk",
    "railway",
    "usage",
    "service",
    "tracks",
    "passenger_lines",
    "maxspeed",
    "maxspeed:forward",
    "maxspeed:backward",
    "gauge",
    "electrified",
    "voltage",
    "frequency",
    "ref",
    "railway:ref",
    "bridge",
    "layer",
    "oneway",
    "railway:preferred_direction",
    "train",
    "public_transport",
    "esr:user",
    "uic_ref",
    "source",
    "fixme",
    "note",
}


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


class Line:
    def __init__(self, coords):
        self.coords = coords
        self.cumulative = [0.0]
        for a, b in zip(coords, coords[1:]):
            self.cumulative.append(self.cumulative[-1] + distance(a, b))

    def project(self, point):
        lat, lon = point
        sx = 111195 * math.cos(math.radians(lat))
        best = (float("inf"), 0.0)
        for i, (a, b) in enumerate(zip(self.coords, self.coords[1:])):
            ax, ay = (a[1] - lon) * sx, (a[0] - lat) * 111195
            bx, by = (b[1] - lon) * sx, (b[0] - lat) * 111195
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            t = min(1, max(0, -(ax * dx + ay * dy) / den)) if den else 0
            off = math.hypot(ax + t * dx, ay + t * dy)
            if off < best[0]:
                best = off, self.cumulative[i] + t * (self.cumulative[i + 1] - self.cumulative[i])
        return best

    def at(self, along):
        import bisect

        along = max(0, min(along, self.cumulative[-1]))
        i = max(0, min(bisect.bisect_right(self.cumulative, along) - 1, len(self.coords) - 2))
        a, b = self.coords[i : i + 2]
        t = (along - self.cumulative[i]) / (self.cumulative[i + 1] - self.cumulative[i])
        p = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
        dx = (b[1] - a[1]) * math.cos(math.radians(p[0]))
        dy = b[0] - a[0]
        norm = math.hypot(dx, dy)
        return p, [dx / norm, dy / norm]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def bootstrap(extracts, routes):
    nodes, ways, tags, provenance = {}, {}, {}, []
    for path in extracts:
        raw = path.read_bytes()
        root = ET.fromstring(raw)
        provenance.append({"filename": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for e in root:
            if e.tag not in {"node", "way"}:
                continue
            ts = {t.get("k"): t.get("v") for t in e.findall("tag") if t.get("k") in TAG_KEYS}
            key = e.get("id")
            if e.tag == "node":
                nodes[key] = [float(e.get("lat")), float(e.get("lon"))]
                if ts:
                    tags[key] = ts
            elif ts.get("railway") == "rail":
                ways[key] = [[n.get("ref") for n in e.findall("nd")], ts]
    route = []
    for path in routes:
        doc = json.loads(path.read_text())
        nodes.update(doc["nodes"])
        for key, (ns, ts) in doc["ways"].items():
            if ts.get("railway") == "rail":
                ways.setdefault(
                    key, [[str(n) for n in ns], {k: v for k, v in ts.items() if k in TAG_KEYS}]
                )
        route.extend([[str(n) for n in edge] for edge in doc["path"]])
    assert all(a[1] == b[0] for a, b in zip(route, route[1:])), "Route must be connected"
    line = Line([nodes[route[0][0]], *[nodes[e[1]] for e in route]])
    route_ways = {e[2] for e in route}
    bounds = [min(p[i] for p in line.coords) for i in (0, 1)] + [
        max(p[i] for p in line.coords) for i in (0, 1)
    ]
    keep = {}
    for key, (ns, ts) in ways.items():
        if not all(n in nodes for n in ns):
            continue
        close = key in route_ways
        if not close:
            for n in ns:
                lat, lon = nodes[n]
                if (
                    bounds[0] - 0.01 <= lat <= bounds[2] + 0.01
                    and bounds[1] - 0.02 <= lon <= bounds[3] + 0.02
                ):
                    if line.project(nodes[n])[0] <= 750:
                        close = True
                        break
        if close:
            keep[key] = [ns, ts]
    required = {n for ns, _ in keep.values() for n in ns}
    required.update(s[2] for s in STATIONS)
    required.update(str(n) for n in range(13676555261, 13676555267))
    assert required <= nodes.keys(), "Missing required station/track nodes"
    snapshot = {
        "nodes": {n: nodes[n] for n in sorted(required)},
        "ways": {w: keep[w] for w in sorted(keep)},
        "node_tags": {n: tags[n] for n in sorted(required) if n in tags},
        "path": route,
    }
    return {
        "schema_version": 1,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "attribution": "© OpenStreetMap contributors; infrastructure map reference: RailsMaps",
        "license": "ODbL 1.0",
        "license_url": "https://www.openstreetmap.org/copyright",
        "map_reference": "https://railsmaps.com/kazakhstan",
        "methodology_reference": "https://railsmaps.com/about",
        "osm_extraction": "Public OpenStreetMap API 0.6 map and relation/full XML; filtered railway=rail snapshot retained below.",
        "osm_api_sources": [
            "https://api.openstreetmap.org/api/0.6/relation/3150970/full",
            "https://api.openstreetmap.org/api/0.6/relation/15464054/full",
            "https://api.openstreetmap.org/api/0.6/map?bbox=71.40,51.175,71.56,51.215",
            "https://api.openstreetmap.org/api/0.6/map?bbox=71.355,51.195,71.41,51.232",
            "https://api.openstreetmap.org/api/0.6/map?bbox=71.46,51.145,71.53,51.185",
            "https://api.openstreetmap.org/api/0.6/map?bbox=71.50,51.10,71.56,51.185",
        ],
        "input_files": provenance,
        "route_method": "Connected shortest rail graph paths, joined at Astana-1 stop node 4091065638; geometry represents mapped track connectivity, not an authorised operational route.",
        "route_evidence": {
            "url": "https://tablo-railways.kz/ru/trip/eda45fd7-feba-11f0-9783-f97735b3ef17",
            "service": "7507",
            "date": "2026-02-26",
            "observation": "Archived search-index timetable lists Nurly Zhol then Astana-1 then Kokshetau. Archived API payload is no longer available.",
            "official_service_list": "https://bilet.railways.kz/post/schedule",
        },
        "track_count_method": "Count transverse intersections with distinct mapped track centre lines; co-located pieces within 1 m are deduplicated. tracks=* is honoured when present. Way count is never used as physical track count. Samples are observations, not proof of continuity or operational permissions.",
        "model_assumptions": {
            "station_track_count": 2,
            "station_track_length_m": 1500,
            "main_track_count_if_ambiguous": 2,
            "traffic": "synthetic, explicitly approved by user",
            "operational_track_assignments": "unknown; simulation only",
        },
        "snapshot": snapshot,
    }


def build(sources, out):
    snap = sources["snapshot"]
    nodes, ways, path = snap["nodes"], snap["ways"], snap["path"]
    assert all(a[1] == b[0] for a, b in zip(path, path[1:])), "Disconnected route"
    line = Line([nodes[path[0][0]], *[nodes[e[1]] for e in path]])
    grid = defaultdict(list)
    for wid, (ns, ts) in ways.items():
        for a, b in zip(ns, ns[1:]):
            p, q = nodes[a], nodes[b]
            for y in range(
                math.floor(min(p[0], q[0]) * 100), math.floor(max(p[0], q[0]) * 100) + 1
            ):
                for x in range(
                    math.floor(min(p[1], q[1]) * 100), math.floor(max(p[1], q[1]) * 100) + 1
                ):
                    grid[x, y].append((wid, a, b, ts))

    def sample(along, half_width, main_only):
        p, tangent = line.at(along)
        sx = 111195 * math.cos(math.radians(p[0]))
        candidate = {}
        for x in range(math.floor(p[1] * 100) - 1, math.floor(p[1] * 100) + 2):
            for y in range(math.floor(p[0] * 100) - 1, math.floor(p[0] * 100) + 2):
                for item in grid[x, y]:
                    candidate[item[:3]] = item
        hits = []
        for wid, a, b, ts in candidate.values():
            if main_only and (ts.get("usage") != "main" or ts.get("service")):
                continue
            ax, ay = (nodes[a][1] - p[1]) * sx, (nodes[a][0] - p[0]) * 111195
            bx, by = (nodes[b][1] - p[1]) * sx, (nodes[b][0] - p[0]) * 111195
            dx, dy = bx - ax, by - ay
            seglen = math.hypot(dx, dy)
            if not seglen or abs((dx * tangent[0] + dy * tangent[1]) / seglen) < 0.85:
                continue
            ta, tb = ax * tangent[0] + ay * tangent[1], bx * tangent[0] + by * tangent[1]
            if ta * tb > 0 or abs(tb - ta) < 1e-9:
                continue
            fraction = -ta / (tb - ta)
            lateral = -(ax + fraction * dx) * tangent[1] + (ay + fraction * dy) * tangent[0]
            if abs(lateral) > half_width:
                continue
            try:
                tracks = int(ts.get("tracks", 1))
            except ValueError:
                tracks = 1
            hits.append(
                {
                    "offset_m": round(lateral, 2),
                    "osm_way_ids": [wid],
                    "tracks_represented": max(1, tracks),
                    "usage": ts.get("usage"),
                    "service": ts.get("service"),
                }
            )
        unique = []
        for hit in sorted(hits, key=lambda h: h["offset_m"]):
            if unique and abs(unique[-1]["offset_m"] - hit["offset_m"]) < 1:
                unique[-1]["osm_way_ids"] = sorted(
                    set(unique[-1]["osm_way_ids"] + hit["osm_way_ids"])
                )
                unique[-1]["tracks_represented"] = max(
                    unique[-1]["tracks_represented"], hit["tracks_represented"]
                )
            else:
                unique.append(hit)
        return {
            "distance_m": round(along, 2),
            "lat": p[0],
            "lon": p[1],
            "half_width_m": half_width,
            "mapped_track_count": sum(h["tracks_represented"] for h in unique),
            "intersections": unique,
        }

    stations = []
    for i, (sid, name, nid) in enumerate(STATIONS):
        off, along = line.project(nodes[nid])
        if i == 0:
            along = 0
        if i == len(STATIONS) - 1:
            along = line.cumulative[-1]
        observation = sample(max(0, min(along + 150, line.cumulative[-1] - 10)), 150, False)
        observation.update(
            {
                "method": "transverse OSM geometry sample 150 m towards Nurly Zhol from station projection; terminal sample clipped to route",
                "operational_usable_track_count": None,
                "warning": "Counts mapped geometry at one cross-section; yard/spur tracks may be present; not complete station inventory or usable capacity.",
            }
        )
        if sid == "NURLY_ZHOL":
            observation["train_stop_position_node_ids"] = [
                str(n) for n in range(13676555261, 13676555267)
            ]
            observation["mapped_train_stop_positions"] = 6
        stations.append(
            {
                "id": sid,
                "name": name,
                "osm_node_id": nid,
                "lat": nodes[nid][0],
                "lon": nodes[nid][1],
                "distance_m": round(along, 2),
                "projection_offset_m": round(off, 2),
                "model_track_count": 2,
                "model_track_length_m": 1500,
                "station_capacity_status": "assumed_for_simulation",
                "source_urls": [f"https://www.openstreetmap.org/node/{nid}"],
                "mapped_track_observation": observation,
            }
        )
    assert all(a["distance_m"] < b["distance_m"] for a, b in zip(stations, stations[1:])), (
        "Station order error"
    )
    sections = []
    for a, b in zip(stations, stations[1:]):
        start, end = a["distance_m"], b["distance_m"]
        samples = [sample(start + (end - start) * f, 40, True) for f in (0.25, 0.5, 0.75)]
        counts = {s["mapped_track_count"] for s in samples}
        observed = counts.pop() if len(counts) == 1 else None
        widlist = sorted(
            {
                e[2]
                for i, e in enumerate(path)
                if line.cumulative[i] < end and line.cumulative[i + 1] > start
            }
        )
        sampleways = {wid for s in samples for h in s["intersections"] for wid in h["osm_way_ids"]}
        maxspeeds = [
            {"osm_way_id": wid, "tag": k, "value": v}
            for wid in sorted(set(widlist) | sampleways)
            for k, v in ways[wid][1].items()
            if k.startswith("maxspeed")
        ]
        count = observed if observed else None
        # Nearby main lines in Astana serve other routes too. A transverse count
        # establishes mapped geometry, never the usable capacity of our route.
        unambiguous_pair = count == 2 and a["id"] != "ASTANA_1"
        sections.append(
            {
                "id": f"SECTION_{a['id']}__{b['id']}",
                "station_a": a["id"],
                "station_b": b["id"],
                "length_m": round(end - start, 2),
                "model_main_track_count": 2,
                "mapped_main_track_count": count,
                "status": "observed_in_osm_samples" if unambiguous_pair else "assumed",
                "osm_way_ids": widlist,
                "source_urls": [
                    f"https://www.openstreetmap.org/way/{w}"
                    for w in sorted(set(widlist) | sampleways)
                ],
                "observed_maxspeed_tags": maxspeeds,
                "mapped_track_samples": samples,
                "operational_track_directions_verified": False,
                "model_capacity_note": "Two tracks in the simulation; nearby mapped main tracks may serve other routes. Route-specific operational capacity is unverified.",
            }
        )
    manifest = {
        "id": "KOKSHETAU_NURLY_ZHOL",
        "name": "Кокшетау-1 ↔ Астана Нурлы Жол через Астана-1",
        "terminal_ids": ["KOKSHETAU_1", "NURLY_ZHOL"],
        "length_m": round(line.cumulative[-1], 2),
        "operational_exactness": False,
        "limitations": [
            "Геометрия и наблюдения путей взяты из OSM; полнота карты и эксплуатационная пригодность не подтверждены КТЖ.",
            "Количество главных путей проверено поперечными срезами карты, а не непрерывной сверкой с ТРА.",
            "На каждой станции 2 модельных пути длиной 1500 м — явное допущение симуляции; фактическая доступная вместимость неизвестна.",
            "Нагрузка синтетическая. Отсутствуют данные фактической занятости, полный график грузовых поездов и блок-участки.",
            "Станционные маршруты, полезные длины, допустимые направления, профиль уклонов и неуказанные скорости требуют исходных данных КТЖ.",
            "Километраж рассчитан вдоль связной геометрии OSM; он не является официальным железнодорожным пикетажем.",
        ],
        "stations": stations,
        "sections": sections,
    }
    features = [
        {
            "type": "Feature",
            "id": "corridor_centerline",
            "properties": {
                "role": "corridor_centerline",
                "source": "OpenStreetMap",
                "length_m": manifest["length_m"],
            },
            "geometry": {"type": "LineString", "coordinates": [[p[1], p[0]] for p in line.coords]},
        }
    ]
    for wid, (ns, ts) in ways.items():
        features.append(
            {
                "type": "Feature",
                "id": f"way/{wid}",
                "properties": {"role": "mapped_railway", "osm_way_id": wid, **ts},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[nodes[n][1], nodes[n][0]] for n in ns],
                },
            }
        )
    for station in stations:
        features.append(
            {
                "type": "Feature",
                "id": station["id"],
                "properties": {
                    "role": "station",
                    "name": station["name"],
                    "osm_node_id": station["osm_node_id"],
                },
                "geometry": {"type": "Point", "coordinates": [station["lon"], station["lat"]]},
            }
        )
    write_json(out / "infrastructure.json", manifest)
    write_json(
        out / "geometry.geojson",
        {"type": "FeatureCollection", "attribution": sources["attribution"], "features": features},
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/corridor")
    parser.add_argument("--extracts", type=Path, nargs="+")
    parser.add_argument("--routes", type=Path, nargs=2)
    args = parser.parse_args()
    if bool(args.extracts) != bool(args.routes):
        parser.error("--extracts and --routes must be supplied together")
    if args.extracts:
        sources = bootstrap(args.extracts, args.routes)
        write_json(args.output / "sources.json", sources)
    else:
        sources = json.loads((args.output / "sources.json").read_text(encoding="utf-8"))
    result = build(sources, args.output)
    print(
        json.dumps(
            {
                "length_m": result["length_m"],
                "stations": len(result["stations"]),
                "sections": len(result["sections"]),
                "track_observations": [
                    [s["id"], s["mapped_main_track_count"], s["status"]] for s in result["sections"]
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
