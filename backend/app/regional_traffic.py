"""Selected archived reference pairs on a shared graph, with synthetic releases."""

import json
from datetime import date

from .advisory.speed import minimum_duration_s
from .planning.common import section_for
from .regional import route_between
from .traffic import REFERENCE


def reference_demand(base, service_date="2026-10-02", multiplier=1):
    day = date.fromisoformat(service_date)
    catalog = json.loads(REFERENCE.read_text())
    scenario = base.model_copy(deep=True)
    names = {s.name: s.id for s in scenario.stations}
    templates = {t.kind: t for t in scenario.trains}
    specs = []
    skipped = []
    for service in catalog["services"]:
        if day.weekday() not in service["weekdays"]:
            continue
        if service.get("frequency") == "every_other_day" and (day - date(2026, 10, 2)).days % 2:
            continue  # illustrative parity, explicitly recorded below
        endpoints = ["OSM_" + s for s in service.get("endpoints", [])] or [
            names.get(s) for s in service["endpoint_names"]
        ]
        try:
            path = route_between(scenario, *endpoints)
        except (ValueError, TypeError):
            skipped.append(service["pair"])
            continue
        for reverse in (False, True):
            specs.append(("passenger", path[::-1] if reverse else path, service["pair"]))
    freight = [t.route for t in base.trains if t.kind == "freight"]
    for i in range(8):
        specs.append(("freight", freight[i % len(freight)], None))
    trains = []
    records = {}
    for replica in range(multiplier):
        for i, (kind, path, pair) in enumerate(specs):
            t = templates[kind].model_copy(deep=True)
            t.id = f"REG-{replica + 1}-{i + 1:02}"
            t.route = path[:]
            t.release_s = int((i * 86400 / len(specs) + replica * 300) % 86400)
            t.due_s = 7 * 86400
            t.due_s = (
                t.release_s
                + sum(
                    minimum_duration_s(t, section_for(scenario, a, b), a)
                    for a, b in zip(path, path[1:])
                )
                + len(path) * t.min_dwell_s
                + 600
            )
            trains.append(t)
            records[t.id] = {
                "reference_pair": pair,
                "departure_time_source": "synthetic",
                "route_mapping": "shortest_connected_osm_path_not_verified_service_stops",
            }
    scenario.trains = trains
    scenario.metadata["traffic"] = {
        "profile": "reference_day",
        "service_date": service_date,
        "train_count": len(trains),
        "reference_month": catalog["published_schedule_month"],
        "reference_url": catalog["source_url"],
        "verified_for_current_date": False,
        "holiday_calendar_verified": False,
        "alternate_day_phase": "synthetic anchor 2026-10-02",
        "skipped_pairs": skipped,
        "records": records,
        "note": "Выбранные пары и периодичность из справочника КТЖ за май 2026. Трассы по связному графу; времена, 8 грузовых рейсов и фаза движения через день — модельные. Полнота расписания не подтверждена.",
    }
    return scenario
