"""Export Kazakhstan railway ways from OpenStreetMap, without station features."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ENDPOINT = "https://overpass-api.de/api/interpreter"
ACTIVE_TYPES = ("rail", "light_rail", "subway", "tram", "narrow_gauge", "monorail", "funicular")
OTHER_TYPES = ("construction", "proposed", "disused", "abandoned", "razed")


def build_query(railway_types, timeout=180):
    """Select only track ways in Kazakhstan's administrative area."""
    if not railway_types or not set(railway_types) <= set(ACTIVE_TYPES + OTHER_TYPES):
        raise ValueError("Unsupported railway types")
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    pattern = "|".join(railway_types)
    return (
        f"[out:json][timeout:{timeout}];\n"
        'area["ISO3166-1"="KZ"]["boundary"="administrative"]["admin_level"="2"]->.country;\n'
        f'way(area.country)["railway"~"^({pattern})$"]["area"!="yes"];\n'
        "out body geom;\n"
    )


def fetch_data(query, endpoint=ENDPOINT, timeout=180):
    request = Request(
        endpoint,
        data=urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": "KTZHproblemsolver-railway-parser/1.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout + 30) as response:
        return json.load(response)


def to_geojson(payload, railway_types):
    """Keep full line geometry, original tags and node IDs; reject partial data."""
    if not isinstance(payload, dict):
        raise ValueError("Expected an Overpass JSON object")
    if payload.get("remark"):
        raise ValueError(f"Overpass returned incomplete data: {payload['remark']}")
    if not isinstance(payload.get("elements"), list):
        raise ValueError("Overpass response is missing elements")
    features = []
    seen = set()
    for element in payload["elements"]:
        tags = element.get("tags", {})
        if (element.get("type") != "way" or tags.get("railway") not in railway_types
                or tags.get("area") == "yes"):
            continue
        osm_id = element["id"]
        if osm_id in seen:
            continue
        geometry = element.get("geometry", [])
        if len(geometry) < 2:
            raise ValueError(f"Way {osm_id} has missing or incomplete geometry")
        coordinates = []
        for point in geometry:
            if not isinstance(point, dict):
                raise ValueError(f"Way {osm_id} has missing coordinates")
            lon, lat = point.get("lon"), point.get("lat")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in (lon, lat)):
                raise ValueError(f"Way {osm_id} has invalid coordinates")
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"Way {osm_id} has out-of-range coordinates")
            coordinates.append([lon, lat])
        if len(set(map(tuple, coordinates))) < 2:
            raise ValueError(f"Way {osm_id} has degenerate geometry")
        features.append({
            "type": "Feature", "id": f"way/{osm_id}",
            "properties": {**tags, "osm_id": osm_id, "osm_type": "way",
                           "osm_nodes": element.get("nodes", [])},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        })
        seen.add(osm_id)
    if not features:
        raise ValueError("No railway lines found; refusing to overwrite output with an empty map")
    return {
        "type": "FeatureCollection",
        "name": "Kazakhstan railway lines (no stations)",
        "attribution": "© OpenStreetMap contributors",
        "license": "https://www.openstreetmap.org/copyright",
        "source": "OpenStreetMap via Overpass API",
        "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
        "features": sorted(features, key=lambda feature: feature["properties"]["osm_id"]),
    }


def write_json(path, data):
    """Replace only after a complete JSON file has been written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/kazakhstan_railways.geojson"))
    parser.add_argument("--input", type=Path, help="Convert saved Overpass JSON without network access")
    parser.add_argument("--save-raw", type=Path, help="Also save the downloaded Overpass JSON")
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--rail-only", action="store_true", help="Only railway=rail (includes sidings/yards)")
    parser.add_argument("--include-inactive", action="store_true", help="Include planned, construction and historical track")
    parser.add_argument("--print-query", action="store_true", help="Print query without downloading")
    args = parser.parse_args(argv)
    railway_types = ("rail",) if args.rail_only else ACTIVE_TYPES
    if args.include_inactive:
        railway_types += OTHER_TYPES
    try:
        query = build_query(railway_types, args.timeout)
        if args.print_query:
            print(query, end="")
            return 0
        paths = [p.resolve() for p in (args.input, args.output, args.save_raw) if p is not None]
        if len(paths) != len(set(paths)):
            raise ValueError("Input, output and raw-data paths must be different")
        if args.input:
            with args.input.open(encoding="utf-8-sig") as source:
                payload = json.load(source)
        else:
            print("Downloading Kazakhstan railway lines from OpenStreetMap...", file=sys.stderr)
            payload = fetch_data(query, args.endpoint, args.timeout)
        result = to_geojson(payload, railway_types)
        if args.save_raw:
            write_json(args.save_raw, payload)
        write_json(args.output, result)
        print(f"Saved {len(result['features'])} railway lines to {args.output}")
        return 0
    except (OSError, URLError, ValueError, KeyError, TypeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
