"""Kokshetau-1 <-> Astana Nurly Zhol: mapped geometry, synthetic operations."""

import json
from pathlib import Path

from backend.app.advisory.speed import minimum_duration_s
from backend.app.realism.configuration import load_profile
from backend.app.realism.incidents import REALISM_INCIDENTS, inject_incident
from backend.app.schemas import (
    Block,
    DavisResistance,
    EntrySpeedLimit,
    MainTrack,
    Plan,
    Scenario,
    Section,
    Station,
    Track,
    Train,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INFRASTRUCTURE = PROJECT_ROOT / "data/corridor/infrastructure.json"
INCIDENTS = (
    "main_track_closure",
    "signal_failure",
    "station_track_closure",
    "train_delay",
    "speed_restriction",
    "single_track_operation",
    "ten_incidents",
    *REALISM_INCIDENTS,
)


def load_infrastructure(path: str | Path = DEFAULT_INFRASTRUCTURE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def audit_infrastructure(infrastructure: dict) -> dict:
    """Map geometry is not an operating track diagram; missing data stays missing."""
    gaps = []
    for station in infrastructure["stations"]:
        if station["station_capacity_status"] != "operator_verified":
            gaps.append(
                {
                    "object": station["id"],
                    "field": "usable_station_tracks",
                    "reason": "Нет подтверждённого ТРА, полезных длин и маршрутов приёма",
                }
            )
    for section in infrastructure["sections"]:
        if section.get("status") != "operator_verified":
            gaps.append(
                {
                    "object": section["id"],
                    "field": "operating_track_configuration",
                    "reason": "OSM не подтверждает блок-участки, направления и стрелочные маршруты",
                }
            )
    return {
        "corridor": infrastructure["name"],
        "geometry_length_km": round(infrastructure["length_m"] / 1000, 3),
        "station_count": len(infrastructure["stations"]),
        "section_count": len(infrastructure["sections"]),
        "operational_exactness": not gaps and infrastructure.get("operational_exactness") is True,
        "traffic_source": "synthetic",
        "gaps": gaps,
        "stations": [
            {
                "id": s["id"],
                "name": s["name"],
                "simulation_tracks": s["model_track_count"],
                "operating_track_count": None,
                "capacity_status": s["station_capacity_status"],
            }
            for s in infrastructure["stations"]
        ],
        "sections": [
            {
                "id": s["id"],
                "mapped_main_tracks": s.get("mapped_main_track_count"),
                "simulation_tracks": s["model_main_track_count"],
                "status": s["status"],
            }
            for s in infrastructure["sections"]
        ],
    }


def corridor_scenario(
    infrastructure_path: str | Path = DEFAULT_INFRASTRUCTURE,
    *,
    train_count: int = 8,
    departure_interval_s: int = 900,
    require_exact: bool = False,
) -> Scenario:
    if not 5 <= train_count <= 40 or departure_interval_s < 60:
        raise ValueError("Use 5..40 synthetic trains and departure interval >= 60 seconds")
    profile = load_profile()
    parameters = profile["parameters"]
    infrastructure = load_infrastructure(infrastructure_path)
    audit = audit_infrastructure(infrastructure)
    if require_exact and not audit["operational_exactness"]:
        raise ValueError(
            "Точная эксплуатационная модель недоступна: отсутствуют ТРА и блок-участки"
        )
    evidence = {}
    stations = []
    for item in infrastructure["stations"]:
        stations.append(
            Station(
                id=item["id"],
                name=item["name"],
                clearance_s=120,
                switch={"id": "throat", "clearance_s": 15},
                tracks=[
                    Track(id=f"SIM-{i + 1}", length_m=item["model_track_length_m"])
                    for i in range(item["model_track_count"])
                ],
            )
        )
        evidence[f"station:{item['id']}"] = {
            "status": item["station_capacity_status"],
            "osm_node_id": item["osm_node_id"],
            "mapped_observation": item.get("mapped_track_observation", {}),
            "note": "SIM-* — ресурсы учебной модели, не реальные номера станционных путей",
        }
    sections = []
    for item in infrastructure["sections"]:
        count = item["model_main_track_count"]
        tracks = [
            MainTrack(
                id=str(i + 1),
                direction=("both" if count == 1 else "a_to_b" if i % 2 == 0 else "b_to_a"),
            )
            for i in range(count)
        ]
        sections.append(
            Section(
                id=item["id"],
                station_a=item["station_a"],
                station_b=item["station_b"],
                length_m=item["length_m"],
                max_speed_mps=80 / 3.6,
                main_tracks=tracks,
                headway_s=60,
                tail_clearance_speed_mps=5,
                grade_permille=profile["section_overrides"]
                .get(item["id"], {})
                .get("grade_permille", profile["segment_defaults"]["grade_permille"]),
            )
        )
        evidence[f"section:{item['id']}"] = {
            "status": item["status"],
            "osm_way_ids": item["osm_way_ids"],
            "mapped_main_track_count": item.get("mapped_main_track_count"),
            "direction_source": "simulation_assumption",
            "speed_source": "simulation_assumption",
            "observed_maxspeed_tags": item.get("observed_maxspeed_tags", []),
        }
    route = [s.id for s in stations]
    trains = []
    for i in range(train_count):
        freight = i % 5 == 2
        # Freight terminates at Astana-1 rather than the Nurly Zhol passenger terminal.
        train_route = [sid for sid in route if sid != "NURLY_ZHOL"] if freight else route[:]
        if i % 2:
            train_route.reverse()
        release = (i // 2) * departure_interval_s + (300 if i % 2 else 0)
        train = Train(
            id=f"SYN-{'F' if freight else 'P'}{i + 1:02}",
            kind="freight" if freight else "passenger",
            priority=1 if freight else 3,
            route=train_route,
            release_s=release,
            due_s=86400,
            min_dwell_s=120 if freight else 60,
            length_m=750 if freight else 350,
            mass_kg=2_000_000 if freight else 500_000,
            max_speed_mps=(60 if freight else 80) / 3.6,
            acceleration_mps2=0.22 if freight else 0.45,
            braking_mps2=0.3 if freight else 0.55,
            auxiliary_power_w=15000 if freight else 35000,
            davis_resistance=DavisResistance(
                a=parameters["RESISTANCE_A"],
                b=parameters["RESISTANCE_B"],
                c=parameters["RESISTANCE_C"],
            ),
        )
        running = sum(
            minimum_duration_s(train, section, a)
            for a, b in zip(train_route, train_route[1:])
            for section in sections
            if {section.station_a, section.station_b} == {a, b}
        )
        train.due_s = release + running + (len(train_route) - 1) * train.min_dwell_s + 600
        trains.append(train)
    horizon = max(172800 if "geometry" in infrastructure else 86400, max(t.due_s for t in trains) + 28800)
    return Scenario(
        id="kokshetau-nurly-zhol",
        horizon_s=horizon,
        evaluation_end_s=min(horizon, 43200),
        stations=stations,
        sections=sections,
        trains=trains,
        metadata={
            "corridor": infrastructure["name"],
            "corridor_key": infrastructure["id"] if "geometry" in infrastructure else "kokshetau",
            "geometry_length_m": infrastructure["length_m"],
            "terminal_ids": infrastructure["terminal_ids"],
            "traffic": {
                "source": "synthetic",
                "train_count": train_count,
                "departure_interval_s": departure_interval_s,
                "note": "Условные поезда SYN-*; это не расписание КТЖ",
            },
            "operational_exactness": False,
            "realism": {
                "source": "vendor/railsim",
                "calibration_status": profile["calibration_status"],
                "parameter_file": "config/railsim.example.json",
                "note": profile["note"],
                "minimum_trains": 5,
            },
            "audit": audit,
            "mapping_evidence": evidence,
            "assumptions": [
                "Станционная вместимость и полезные длины заданы для симуляции и требуют ТРА",
                "Номера и направления главных путей условные; количество сопоставлено с OSM",
                "Каждый путь перегона эксклюзивен; автоблокировка внутри перегона не моделируется",
                "Остановка на каждой моделируемой станции, скорость не выше 80 км/ч",
                "Грузовые синтетические рейсы не заходят на пассажирский терминал Нурлы Жол",
                "Запас до целевого конечного прибытия 10 минут, ровный профиль пути",
                "Сопротивление Davis A+Bv+Cv² из railsim; коэффициенты учебные, рекуперация не задана",
            ],
        },
    )


def incident_scenario(base: Scenario, previous: Plan, kind: str) -> Scenario:
    """Inject a future disturbance after the first train has departed."""
    if kind not in INCIDENTS:
        raise ValueError(f"Unknown incident: {kind}")
    if kind in REALISM_INCIDENTS:
        return inject_incident(base, previous, kind)
    scenario = base.model_copy(deep=True)
    first = min(previous.movements, key=lambda m: m.start_s)
    scenario.now_s = first.start_s + max(1, (first.end_s - first.start_s) // 3)
    scenario.state_version += 1
    target_train = next(t for t in scenario.trains if t.id == first.train_id)
    candidates = [
        m
        for m in previous.movements
        if m.train_id == first.train_id and m.start_s > scenario.now_s + 1800
    ]
    if not candidates:
        raise ValueError("The incident requires a longer remaining route")
    target = next((m for m in candidates if m.origin in ("BURABAY", "SHORTANDY")), candidates[0])
    section = next(s for s in scenario.sections if s.id == target.section_id)
    main_resource = f"main_track:{section.id}:{target.main_track_id}"
    until = target.start_s + 1800
    info = {
        "kind": kind,
        "detected_s": scenario.now_s,
        "resource": main_resource,
        "expected_resolution": "Пересчитать будущие резервирования, сохранить начатые движения",
    }
    if kind in ("main_track_closure", "signal_failure", "single_track_operation"):
        scenario.blocks.append(
            Block(
                id=kind,
                resource=main_resource,
                start_s=scenario.now_s,
                end_s=until,
                kind="signal" if kind == "signal_failure" else "closure",
            )
        )
        if kind == "single_track_operation":
            others = [t for t in section.main_tracks if t.id != target.main_track_id]
            if not others:
                raise ValueError("This incident needs at least two main tracks")
            for track in others:
                track.direction = "both"
            info["reverse_movement_permission"] = (
                "Explicit synthetic permission, not inferred from a closure"
            )
            info["expected_resolution"] = (
                "Разрешённый второй путь в обоих направлениях с исключением встречных конфликтов"
            )
        elif kind == "signal_failure":
            info["expected_resolution"] = "Запретить новые входы; сохранить уже начатое движение"
        else:
            info["expected_resolution"] = (
                "Ожидание открытия пути; движение по встречному пути само не разрешается"
            )
    elif kind == "station_track_closure":
        stop = next(
            s
            for s in previous.stops
            if s.train_id == target.train_id and s.station_id == target.destination
        )
        info["resource"] = f"track:{stop.station_id}:{stop.track_id}"
        scenario.blocks.append(
            Block(
                id=kind,
                resource=info["resource"],
                start_s=scenario.now_s,
                end_s=stop.departure_s + 1800,
            )
        )
        info["expected_resolution"] = "Другой доступный станционный путь либо ожидание"
    elif kind == "train_delay":
        target_train.not_before_s[target.origin] = target.start_s + 1800
        info.update(
            train_id=target_train.id,
            station_id=target.origin,
            expected_resolution="Учесть задержку на 30 минут и пересчитать очередность",
        )
    elif kind == "speed_restriction":
        section.entry_speed_limits.append(EntrySpeedLimit(id=kind,start_s=scenario.now_s+1,end_s=scenario.horizon_s,speed_factor=min(1,(40/3.6)/section.max_speed_mps),reason="Объявленное ограничение до 40 км/ч для новых входов"))
        info.update(
            speed_limit_kmh=40,
            expected_resolution="Увеличить ходовое время и пересчитать допустимый профиль скорости",
        )
    elif kind == "ten_incidents":
        upcoming = [m for m in previous.movements if m.start_s > scenario.now_s + 1800]
        seen = set()
        for movement in upcoming:
            resource = f"main_track:{movement.section_id}:{movement.main_track_id}"
            if resource in seen:
                continue
            seen.add(resource)
            kind_of_block="signal" if len(seen)%2 else "closure"
            activates=scenario.now_s
            if kind_of_block=="closure":
                from backend.app.planning.common import clearance_s
                affected_section=next(s for s in scenario.sections if s.id==movement.section_id)
                train_map={t.id:t for t in scenario.trains}
                activates=max([activates]+[m.end_s+clearance_s(train_map[m.train_id],affected_section) for m in previous.movements if m.section_id==movement.section_id and m.main_track_id==movement.main_track_id and m.start_s<=scenario.now_s])
                if activates>scenario.now_s:
                    scenario.blocks.append(Block(id=f"incident-{len(seen)}-entry",resource=resource,start_s=scenario.now_s,end_s=activates,kind="signal"))
            scenario.blocks.append(Block(id=f"incident-{len(seen)}",resource=resource,start_s=activates,end_s=max(activates+900,movement.start_s+900),kind=kind_of_block))
            if len(seen) == 10:
                break
        if len(seen) != 10:
            raise ValueError("Scenario has fewer than ten future resources")
        info["occupied_track_procedure"]="Немедленный запрет новых входов; объявленное закрытие после освобождения хвостом. Аварийная остановка внутри перегона не подразумевается."
        info["expected_resolution"] = (
            "Совместный пересчёт десяти ограничений без изменения начатых движений"
        )
    scenario.metadata["incident"] = info
    return Scenario.model_validate(scenario.model_dump())
