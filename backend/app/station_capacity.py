"""Explicit operational assumptions, separate from immutable OSM source files."""

import json
from pathlib import Path

from .schemas import Track


def expanded_stations(scenario):
    config = json.loads(
        (Path(__file__).resolve().parents[2] / "config/station_capacity.json").read_text()
    )
    scenario = scenario.model_copy(deep=True)
    changes = {}
    for station in scenario.stations:
        evidence = scenario.metadata["mapping_evidence"]["station:" + station.id]
        count = config["stations"].get(str(evidence["osm_node_id"]), len(station.tracks))
        for i in range(len(station.tracks), count):
            station.tracks.append(Track(id=f"SIM-{i + 1}", length_m=config["track_length_m"]))
        if str(evidence["osm_node_id"]) in config["stations"]:
            changes[station.id] = len(station.tracks)
            evidence["capacity_source"] = config["source"]
            evidence["simulated_tracks"] = len(station.tracks)
    for station in scenario.metadata.get("audit", {}).get("stations", []):
        if station.get("station_id", station.get("id")) in changes:
            station["simulation_tracks"] = changes[station.get("station_id", station.get("id"))]
    scenario.metadata["station_capacity"] = {
        "counts": changes,
        "note": config["note"],
        "verified_by_operator": False,
    }
    return scenario
