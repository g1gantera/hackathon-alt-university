"""Reproducible day demand: archived passenger references + synthetic freight.

Neither departure times nor the freight volume are observations of KTZ traffic.
"""

import json
from datetime import date
from pathlib import Path

from .advisory.speed import minimum_duration_s
from .planning.common import section_for
from .traffic_control import CATEGORIES

REFERENCE = Path(__file__).resolve().parents[2] / "data/traffic/ktzh_reference.json"


def daily_demand(base, service_date="2026-10-02", multiplier=1, holiday=False):
    if multiplier not in (1, 2, 3):
        raise ValueError("Load multiplier must be 1, 2 or 3")
    day = date.fromisoformat(service_date)
    catalog = json.loads(REFERENCE.read_text())
    scenario = base.model_copy(deep=True)
    corridor = scenario.metadata.get("corridor_key", "kokshetau")
    route = [s.id for s in scenario.stations]
    evidence = scenario.metadata["mapping_evidence"]
    by_osm = {str(evidence["station:" + s.id]["osm_node_id"]): s.id for s in scenario.stations}
    by_name = {s.name: s.id for s in scenario.stations}
    specs = []
    for service in catalog["services"]:
        if (
            corridor not in service["corridors"]
            or day.weekday() not in service["weekdays"]
            or holiday
            and service.get("except_holidays")
        ):
            continue
        endpoints = [by_osm.get(s) for s in service.get("endpoints", [])] or [
            by_name.get(s) for s in service["endpoint_names"]
        ]
        exact = len(endpoints) == 2 and all(endpoints)
        if exact:
            a, b = sorted(route.index(s) for s in endpoints)
            path = route[a : b + 1]
        else:
            path = route[:]
        for direction in (1, -1):
            specs.append(
                (
                    "passenger",
                    path if direction == 1 else path[::-1],
                    dict(
                        reference_pair=service["pair"],
                        reference_route=service["route"],
                        route_mapping="endpoints_present" if exact else "partial_route_assumption",
                    ),
                )
            )
    if not specs:
        # No route pair in the selected source covers this corridor.
        specs = [("passenger", route[:], {}), ("passenger", route[::-1], {})]
    freight_route = [s for s in route if s != "NURLY_ZHOL"]
    for i in range(8):
        specs.append(("freight", freight_route if i % 2 == 0 else freight_route[::-1], {}))
    templates = {t.kind: t for t in scenario.trains}
    trains = []
    records = {}
    for replica in range(multiplier):
        for index, (kind, path, reference) in enumerate(specs):
            t = templates[kind].model_copy(deep=True)
            t.id = f"DAY-{'P' if kind == 'passenger' else 'F'}-{replica + 1}-{index + 1:02}"
            t.route = list(path)
            t.release_s = int((index * 86400 / len(specs) + replica * 300) % 86400)
            t.not_before_s = {}
            t.manual_station_tracks = {}
            t.manual_main_tracks = {}
            t.section_hold_s = {}
            t.section_recovery_all_tracks = []
            t.dispatch_category = kind
            t.priority = CATEGORIES[kind][1]
            t.due_s = t.release_s + 7 * 86400
            t.due_s = (
                t.release_s
                + sum(
                    minimum_duration_s(t, section_for(scenario, a, b), a)
                    for a, b in zip(t.route, t.route[1:])
                )
                + (len(path) - 1) * t.min_dwell_s
                + 600
            )
            trains.append(t)
            records[t.id] = {
                **reference,
                "departure_time_source": "synthetic",
                "flow_source": "archived_route_reference" if reference else "synthetic",
                "replica": replica + 1,
            }
    scenario.trains = trains
    scenario.horizon_s = max(7 * 86400, max(t.due_s for t in trains) + 86400)
    scenario.evaluation_end_s = 86400
    scenario.metadata["traffic"] = {
        "profile": "reference_day",
        "source": "mixed_reference_synthetic",
        "service_date": service_date,
        "weekday": day.weekday(),
        "holiday_assumed": holiday,
        "holiday_calendar_verified": False,
        "reference_month": catalog["published_schedule_month"],
        "reference_url": catalog["source_url"],
        "verified_for_current_date": False,
        "multiplier": multiplier,
        "train_count": len(trains),
        "synthetic_freight_trains": 8 * multiplier,
        "records": records,
        "note": "Маршруты и периодичность из таблицы КТЖ за май 2026. Времена, грузовой поток и дополнительные копии синтетические; полнота суточного графика не подтверждена.",
    }
    return scenario
