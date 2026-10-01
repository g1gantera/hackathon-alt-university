"""Model signals and explanations derived from validated reservations.

No real signal inventory or infrastructure owner's operating rules are claimed.
"""

from .control_mode import authorized as route_authorized

CATEGORIES = {
    "emergency": ("Аварийный / особого назначения", 100),
    "passenger": ("Пассажирский", 70),
    "express_freight": ("Ускоренный грузовой", 40),
    "freight": ("Обычный грузовой", 20),
    "service": ("Хозяйственный / одиночный локомотив", 5),
}


def category(train):
    return train.get("dispatch_category") or train.get("kind", train.get("type", "freight"))


def yield_intervals(state):
    plan = state["active_plan"]
    trains = {t["id"]: t for t in state["scenario"]["trains"]}
    stops = {(s["train_id"], s["station_id"]): s for s in plan["_native"]["stops"]}
    reference = {
        (s["train_id"], s["station_id"]): s["departure_s"]
        for s in state.get("baseline", plan)["_native"]["stops"]
    }
    result = []
    for move in plan["movements"]:
        train = trains[move["train_id"]]
        stop = stops[(train["id"], move["origin"])]
        ready = max(
            stop["arrival_s"] + train["min_dwell_s"],
            train.get("not_before_s", {}).get(move["origin"], 0),
            train["release_s"],
        )
        if move["start_s"] <= ready:
            continue
        blockers = [
            m
            for m in plan["movements"]
            if m["train_id"] != train["id"]
            and m["section_id"] == move["section_id"]
            and m["main_track_id"] == move["main_track_id"]
            and m["release_s"] > ready
            and m["start_s"] < move["start_s"]
        ]
        for other in blockers:
            winner = trains[other["train_id"]]
            higher = CATEGORIES[category(winner)][1] > CATEGORIES[category(train)][1]
            late = (
                max(m["end_s"] for m in plan["movements"] if m["train_id"] == winner["id"])
                > winner["due_s"]
            )
            # Priority is an input to optimization, not proof of its causal choice.
            context = "; категория пропускаемого поезда выше" if higher else ""
            context += "; у пропускаемого поезда прогнозируется опоздание" if late else ""
            base_departure = reference.get((train["id"], move["origin"]), ready)
            if ready < base_departure:
                context += f"; запас до исходного отправления {base_departure - ready:.0f} с"
            result.append(
                dict(
                    reference_departure_s=base_departure,
                    train_id=train["id"],
                    to_train_id=winner["id"],
                    station_id=move["origin"],
                    track_id=stop["track_id"],
                    section_id=move["section_id"],
                    main_track_id=move["main_track_id"],
                    start_s=ready,
                    end_s=min(move["start_s"], other["release_s"]),
                    reason="Путь зарезервирован другим поездом; требуется освобождение хвостом и защитный интервал"
                    + context,
                )
            )
    return result


def control_state(state, fleet, switches, applicable):
    now = state["sim_time_s"]
    intervals = yield_intervals(state)
    for train in fleet:
        native = next(t for t in state["scenario"]["trains"] if t["id"] == train["id"])
        train["dispatch_category"] = category(native)
        train["category_label"] = CATEGORIES[category(native)][0]
        active = [
            e
            for e in intervals
            if e["train_id"] == train["id"] and e["start_s"] <= now < e["end_s"]
        ]
        train["yielding"] = active
        if active:
            train["waiting_for"] = list(dict.fromkeys(e["to_train_id"] for e in active))
            train["wait_reason"] = (
                "Уступает " + ", ".join(train["waiting_for"]) + ": " + active[0]["reason"]
            )
        next_move = next(
            (
                m
                for m in state["active_plan"]["movements"]
                if m["train_id"] == train["id"] and now < m["start_s"] <= now + 0.01
            ),
            None,
        )
        if next_move and not route_authorized(state, next_move):
            train["wait_reason"] = (
                "Ожидает разрешения диспетчера на отправление по назначенному маршруту"
            )
            train["waiting_for"] = []
    signals = []
    for section in state["scenario"]["sections"]:
        for track in section["main_tracks"]:
            for origin, destination, direction in [
                (section["station_a"], section["station_b"], "a_to_b"),
                (section["station_b"], section["station_a"], "b_to_a"),
            ]:
                if track["direction"] not in ("both", direction):
                    continue
                departures = [
                    m
                    for m in state["active_plan"]["movements"]
                    if m["section_id"] == section["id"]
                    and m["main_track_id"] == track["id"]
                    and m["origin"] == origin
                ]
                authorized = next(
                    (
                        m
                        for m in departures
                        if m["start_s"] <= now < min(m["end_s"], m["start_s"] + 15)
                    ),
                    None,
                )
                lock = next((s for s in switches if s["station_id"] == origin), None)
                blocked = any(
                    b["resource"]
                    in (f"section:{section['id']}", f"main_track:{section['id']}:{track['id']}")
                    and b["start_s"] <= now < b["end_s"]
                    for b in state["scenario"]["blocks"]
                )
                green = bool(
                    applicable
                    and authorized
                    and route_authorized(state, authorized)
                    and not blocked
                    and (
                        not lock
                        or not lock["blocked_by"]
                        and lock["train_id"] in (None, authorized["train_id"])
                    )
                )
                signals.append(
                    dict(
                        id=f"signal:{section['id']}:{track['id']}:{origin}",
                        station_id=origin,
                        section_id=section["id"],
                        main_track_id=track["id"],
                        aspect="green" if green else "red",
                        train_id=authorized["train_id"] if green else None,
                        synthetic=True,
                        type="exit",
                        offset_m=250,
                        reason="Разрешён проверенный маршрут назначенному поезду"
                        if green
                        else "Проезд запрещён: нет разрешённого отправления или маршрут ограничен",
                    )
                )
    # Entry and advance-warning heads are tied to an already reserved arrival.
    # No intermediate block or shunting permissions are invented by these symbols.
    for section in state["scenario"]["sections"]:
        for track in section["main_tracks"]:
            for origin, destination, direction in [
                (section["station_a"], section["station_b"], "a_to_b"),
                (section["station_b"], section["station_a"], "b_to_a"),
            ]:
                if track["direction"] not in ("both", direction):
                    continue
                move = next(
                    (
                        m
                        for m in state["active_plan"]["movements"]
                        if m["section_id"] == section["id"]
                        and m["main_track_id"] == track["id"]
                        and m["destination"] == destination
                        and max(m["start_s"], m["end_s"] - 30) <= now < m["end_s"] + 15
                    ),
                    None,
                )
                green = bool(applicable and move and route_authorized(state, move))
                for kind, offset in [("entry", 250), ("warning", 1000)]:
                    signals.append(
                        dict(
                            id=f"{kind}:{section['id']}:{track['id']}:{destination}",
                            type=kind,
                            station_id=destination,
                            section_id=section["id"],
                            main_track_id=track["id"],
                            offset_m=min(offset, section["length_m"] / 3),
                            aspect="green" if green else "yellow" if kind == "warning" else "red",
                            train_id=move["train_id"] if green else None,
                            synthetic=True,
                            reason=(
                                "Приём по проверенному маршруту"
                                if green
                                else "Предупреждение: входной закрыт"
                                if kind == "warning"
                                else "Вход запрещён: нет разрешённого приёма"
                            ),
                        )
                    )
    return signals, intervals


def journal_events(state, intervals):
    """Include crossed interval boundaries even when the clock advances by 60 s."""
    events = []
    for item in intervals:
        for kind, when in [("yield.started", item["start_s"]), ("yield.finished", item["end_s"])]:
            if when <= state["sim_time_s"]:
                events.append(
                    {
                        **item,
                        "type": kind,
                        "sim_time_s": when,
                        "id": f"{kind}:{item['train_id']}:{item['to_train_id']}:{item['section_id']}:{when}",
                    }
                )
    return sorted(events, key=lambda e: e["sim_time_s"])


def dispatch_weight(train, strategy):
    if train.dispatch_category:
        return CATEGORIES[train.dispatch_category][1]
    return train.priority * (5 if strategy == "passenger" and train.kind == "passenger" else 1)
