"""Display bindings for model resources, not an operational OSM routing graph.

OSM supplies the background and station anchors. Parallel running lines use
interpolated OSM cross-section spacing, with an explicit schematic fallback.
Neither this geometry nor the number of mapped sidings changes model capacity.
"""

import math

import numpy as np


def offset_point(point, before, after, offset):
    scale = 111195 * math.cos(math.radians(point[1]))
    dx, dy = (after[0] - before[0]) * scale, (after[1] - before[1]) * 111195
    length = math.hypot(dx, dy) or 1
    return [point[0] - offset * dy / length / scale, point[1] + offset * dx / length / 111195]


def closest_point(point, lines):
    scale = 111195 * math.cos(math.radians(point[1]))
    candidates = []
    for points in lines:
        for a, b in zip(points, points[1:]):
            dx, dy = (b[0] - a[0]) * scale, (b[1] - a[1]) * 111195
            ratio = max(
                0,
                min(
                    1,
                    ((point[0] - a[0]) * scale * dx + (point[1] - a[1]) * 111195 * dy)
                    / (dx * dx + dy * dy or 1),
                ),
            )
            p = [a[0] + ratio * (b[0] - a[0]), a[1] + ratio * (b[1] - a[1])]
            candidates.append(
                (math.hypot((p[0] - point[0]) * scale, (p[1] - point[1]) * 111195), p)
            )
    return min(candidates)[1] if candidates else point


def display_tracks(stations, sections, scenario, infra, features):
    ways = {
        str(f["properties"].get("osm_way_id")): f["geometry"]["coordinates"]
        for f in features
        if f["geometry"]["type"] == "LineString"
    }
    for i, station in enumerate(stations):
        mapped = infra["stations"][i]["mapped_track_observation"]
        hits = mapped["intersections"]
        # Prefer two distinct main rails, not arbitrary industrial sidings.
        preferred = sorted(
            hits,
            key=lambda h: (h.get("usage") != "main" or bool(h.get("service")), abs(h["offset_m"])),
        )[: station["tracks"]]
        preferred.sort(key=lambda h: h["offset_m"])
        section = sections[min(i, len(sections) - 1)]
        points = section["geometry"]
        before, after = points[:2] if i < len(sections) else points[-2:]
        tracks = []
        for lane, native in enumerate(scenario.stations[i].tracks):
            hit = preferred[lane] if lane < len(preferred) else None
            offset = hit["offset_m"] if hit else (lane - (station["tracks"] - 1) / 2) * 6
            anchor = offset_point(station["coordinate"], before, after, offset)
            ids = hit["osm_way_ids"] if hit else []
            lines = [ways[str(w)] for w in ids if str(w) in ways]
            tracks.append(
                {
                    "id": native.id,
                    "coordinate": closest_point(anchor, lines),
                    "osm_way_ids": ids,
                    "length_m": native.length_m,
                    "geometry_source": "osm_anchor_model_assignment" if lines else "schematic",
                }
            )
        station.update(track_layout=tracks, mapped_tracks=mapped["mapped_track_count"])
    for section, source in zip(sections, infra["sections"]):
        points = section["geometry"]
        samples = []
        for sample in source.get("mapped_track_samples", []):
            hits = sorted(sample["intersections"], key=lambda h: abs(h["offset_m"]))
            offsets = sorted(h["offset_m"] for h in hits[: len(section["main_tracks"])])
            if len(offsets) == len(section["main_tracks"]) and all(
                b - a >= 3 for a, b in zip(offsets, offsets[1:])
            ):
                samples.append((sample["distance_m"], offsets))
        start = next(s["position_m"] for s in stations if s["id"] == section["from_station"])
        end = next(s["position_m"] for s in stations if s["id"] == section["to_station"])
        distances = [0.0]
        for a, b in zip(points, points[1:]):
            distances.append(
                distances[-1]
                + math.hypot((b[0] - a[0]) * math.cos(math.radians(a[1])), b[1] - a[1])
            )
        for lane, track in enumerate(section["main_tracks"]):
            geometry = []
            for j, point in enumerate(points):
                along = start + distances[j] / (distances[-1] or 1) * (end - start)
                offset = (
                    float(np.interp(along, [s[0] for s in samples], [s[1][lane] for s in samples]))
                    if samples
                    else (lane - (len(section["main_tracks"]) - 1) / 2) * 6
                )
                geometry.append(
                    offset_point(
                        point, points[max(0, j - 1)], points[min(len(points) - 1, j + 1)], offset
                    )
                )
            track.update(
                geometry=geometry,
                geometry_source="schematic_osm_spacing" if samples else "schematic",
            )


def dispatch_state(train, stops, moves, all_moves, now):
    """Project reservations and explain the next departure without rescheduling."""
    first, last = stops[0], stops[-1]
    active = next((s for s in stops if s["arrival_s"] <= now < s["departure_s"]), None)
    moving = next((m for m in moves if m["start_s"] <= now < m["end_s"]), None)
    next_move = next((m for m in moves if m["start_s"] > now), None)
    result = {
        "on_network": bool(active or moving),
        "station_track_id": active["track_id"] if active else None,
        "main_track_id": moving["main_track_id"] if moving else None,
        "admission_s": first["arrival_s"],
        "next_departure_s": next_move["start_s"] if next_move else None,
        "waiting_for": [],
        "wait_reason": None,
        "departure_track_id": None,
        "arrival_track_id": None,
    }
    if moving:
        result.update(
            departure_track_id=next(
                s["track_id"] for s in stops if s["station_id"] == moving["origin"]
            ),
            arrival_track_id=next(
                s["track_id"] for s in stops if s["station_id"] == moving["destination"]
            ),
        )
    elif now < train["release_s"]:
        result.update(status="scheduled", wait_reason="Ещё не готов к отправлению по расписанию")
    elif now < first["arrival_s"]:
        result.update(
            status="queued", wait_reason="Ожидает допуска на участок; станционный путь ещё не занят"
        )
    elif now >= last["departure_s"]:
        result.update(status="completed", wait_reason="Завершил рейс и освободил станционный путь")
    elif active and next_move:
        ready = max(now, active["arrival_s"] + train["min_dwell_s"])
        # These trains actually precede us on the same reserved main track.
        blockers = sorted(
            (
                m
                for m in all_moves
                if m["train_id"] != train["id"]
                and m["section_id"] == next_move["section_id"]
                and m["main_track_id"] == next_move["main_track_id"]
                and m["release_s"] > ready
                and m["start_s"] < next_move["start_s"]
            ),
            key=lambda m: m["start_s"],
        )
        result["waiting_for"] = list(dict.fromkeys(m["train_id"] for m in blockers))
        if now < active["arrival_s"] + train["min_dwell_s"]:
            result["wait_reason"] = "Минимальная технологическая стоянка"
        elif blockers:
            result["wait_reason"] = (
                "Пропускает "
                + ", ".join(result["waiting_for"])
                + "; ожидает освобождения пути и защитного интервала"
            )
        else:
            result["wait_reason"] = "Ожидает времени отправления с учётом ограничений маршрута"
    return result
