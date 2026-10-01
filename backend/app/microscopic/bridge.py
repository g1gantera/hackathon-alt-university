"""Replay integrated service intentions against the existing OSM edge graph.

This deliberately does not equate SIM tracks with surveyed map edges. Import
is atomic: unresolved stations/routes reject the complete import, not trains.
"""

import json
from functools import lru_cache
from pathlib import Path

from .engine import Engine
from .models import Settings, Stop, TrainSpec
from .network import Network

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def network():
    return Network()


def from_integrated(state):
    from ..corridors import CORRIDORS
    from ..integration import validate_plan

    if state["engine"] != "logic" or state["sim_time_s"] != 0:
        raise ValueError(
            "Детальное исполнение импортируется до начала движения. Сбросьте основной запуск."
        )
    if (
        state["scenario"]["blocks"]
        or any(s.get("entry_speed_limits") for s in state["scenario"]["sections"])
        or any(t.get("section_hold_s") for t in state["scenario"]["trains"])
    ):
        raise ValueError(
            "Импорт ограничений и восстановительных стоянок ещё не подключён. Используйте график без сбоев; сбои задайте в детальном режиме."
        )
    if state["awaiting_plan"] or validate_plan(state, state["active_plan"]):
        raise ValueError("Сначала примените допустимый план integrated.")
    key = state["topology"]["corridor_id"]
    infra = json.loads(CORRIDORS[key][1].read_text())
    net = network()
    osm_stations = {str(s["osm_id"]): s for s in net.stations}
    vertices = {}
    for station in infra["stations"]:
        observed = osm_stations.get(str(station.get("osm_node_id")))
        if (
            observed
            and observed["vertex"] is not None
            and observed.get("snap_dist_m", float("inf")) <= 1000
        ):
            vertices[station["id"]] = observed["vertex"]
    engine = Engine(net, config=Settings())
    native = state["active_plan"]["_native"]
    stops = {(s["train_id"], s["station_id"]): s for s in native["stops"]}
    errors = []
    for train in state["scenario"]["trains"]:
        try:
            missing = [sid for sid in train["route"] if sid not in vertices]
            if missing:
                raise ValueError("Нет допустимой привязки станции к графу: " + ", ".join(missing))
            route = train["route"]
            spec = TrainSpec(
                id=train["id"],
                name=train["id"],
                origin=vertices[route[0]],
                destination=vertices[route[-1]],
                departure_s=stops[train["id"], route[0]]["departure_s"],
                scheduled_arrival_s=stops[train["id"], route[-1]]["arrival_s"],
                importance=max(1, min(10, round(1 + 9 * (train["priority"] - 1) / 99))),
                length_m=train["length_m"],
                mass_t=train["mass_kg"] / 1000,
                max_speed_kmh=train["max_speed_mps"] * 3.6,
                acceleration_mps2=train["acceleration_mps2"],
                braking_mps2=train["braking_mps2"],
                stops=[
                    Stop(
                        vertex=vertices[sid],
                        dwell_s=train["min_dwell_s"],
                        scheduled_arrival_s=stops[train["id"], sid]["arrival_s"],
                        earliest_departure_s=stops[train["id"], sid]["departure_s"],
                    )
                    for sid in route[1:-1]
                ],
            )
            engine.add_train(spec, replan=False)
        except ValueError as error:
            errors.append(f"{train['id']}: {error}")
    if errors:
        engine.history.db.close()
        raise ValueError("Импорт отменён, ни один поезд не пропущен. " + "; ".join(errors))
    engine.replan("imported integrated timetable intentions")
    return engine


def geometry(engine):
    # Include adjacent sidings so newly chosen loops can be understood on the map.
    edges = {a[0] for t in engine.trains.values() for a in t.route}
    vertices = {
        v for eid in edges for v in (engine.network.edges[eid]["u"], engine.network.edges[eid]["v"])
    }
    edges.update(
        eid for v in vertices for eid in engine.network.adj[v] if eid in engine.network.allowed
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"edge": eid, "category": engine.network.edges[eid]["category"]},
                "geometry": {
                    "type": "LineString",
                    "coordinates": engine.network.edges[eid]["geometry"],
                },
            }
            for eid in sorted(edges)
        ],
    }
