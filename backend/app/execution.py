"""One authoritative movement ledger over integrated's explicitly modelled network.

The era fixed-step braking integrator executes native solver intentions. Actual
occupancy, not timetable progress, controls block admission and receiving tracks.
A replanning boundary drains entered legs before changing future assignments.
"""

import copy
import math

from .advisory.speed import section_at_entry
from .control_mode import clearance_key
from .integration import LogicSimulator, config_for, scenario_for
from .live_logic import actual_metrics
from .microscopic.kinematics import integrate
from .planning.common import clearance_s
from .schemas import Section


class UnifiedSimulator(LogicSimulator):
    def reset(self):
        super().reset()
        self.state["planning_budget_s"] = (
            15 if self.state["scenario"]["metadata"].get("network") else 10
        )
        for section in self.state["scenario"]["sections"]:
            if not section["block_length_m"]:
                section["block_length_m"] = 2000
        self.state["baseline_scenario"] = copy.deepcopy(self.state["scenario"])
        self.state["execution"] = {"trains": {}, "events": [], "conflicts": [], "revision": 0}
        self.state["execution"]["throats"] = {}
        self._cache_key = None
        self._refresh()
        self._ensure_trains()

    def _refresh(self):
        key = (self.state["constraint_version"], self.state["active_plan"]["id"])
        if key == self._cache_key:
            return
        self._cache_key = key
        self.trains = {t.id: t for t in scenario_for(self.state).trains}
        self.sections = {
            s["id"]: Section.model_validate(s) for s in self.state["scenario"]["sections"]
        }
        self.stations = {s["id"]: s for s in self.state["scenario"]["stations"]}
        self.moves = {
            tid: sorted(
                (m for m in self.state["active_plan"]["movements"] if m["train_id"] == tid),
                key=lambda m: m["leg"],
            )
            for tid in self.trains
        }
        self.cruise = {
            key: max((p["speed_mps"] for p in profile["points"]), default=0.0)
            for key, profile in self.state["active_plan"].get("_profiles", {}).items()
        }
        self.stops = {
            (s["train_id"], s["station_id"]): s for s in self.state["active_plan"]["stops"]
        }

    @property
    def live(self):
        return self.state["execution"]["trains"]

    def _ensure_trains(self):
        for tid in self.trains:
            self.live.setdefault(
                tid,
                dict(
                    leg=0,
                    x=0.0,
                    v=0.0,
                    energy=0.0,
                    gross=0.0,
                    recovered=0.0,
                    moving=False,
                    admitted=False,
                    complete=False,
                    arrival=None,
                    ready=0.0,
                    release=0.0,
                    move=None,
                    track=None,
                    authority=0.0,
                    blockers=[],
                    reason="Ожидание отправления",
                    distance=0.0,
                    actual_moves=[],
                    actual_stops=[],
                    waiting=0.0,
                    frames=[],
                ),
            )
        for tid in set(self.live) - self.trains.keys():
            if self.live[tid]["moving"]:
                raise ValueError("Cannot remove a moving train")
            del self.live[tid]

    def event(self, kind, tid, reason, others=()):
        events = self.state["execution"]["events"]
        events.append(
            dict(
                id=f"actual:{self.state['epoch']}:{self.state['execution']['revision']}",
                type=kind,
                train_id=tid,
                to_train_id=next(iter(others), None),
                sim_time_s=self.state["sim_time_s"],
                reason=reason,
                message=reason,
            )
        )
        self.state["execution"]["revision"] += 1
        del events[:-500]

    def hold(self, tid, reason, blockers=()):
        r = self.live[tid]
        if (reason, list(blockers)) != (r["reason"], r["blockers"]):
            self.event("train.yielding", tid, reason, blockers)
        r.update(reason=reason, blockers=list(blockers))
        if (
            blockers
            and not r["moving"]
            and not r["complete"]
            and self.state["sim_time_s"] > self.moves[tid][r["leg"]]["start_s"] + 5
        ):
            self.state["awaiting_plan"] = True

    def blocked(self, resource):
        now = self.state["sim_time_s"]
        return any(
            b["resource"] == resource and b["start_s"] <= now < b["end_s"]
            for b in self.state["scenario"]["blocks"]
        )

    def roster_ready(self, tid):
        now = self.state["sim_time_s"]
        for unit in self.state["scenario"]["metadata"].get("resource_roster", {}).get("units", []):
            if tid not in unit["train_ids"]:
                continue
            index = unit["train_ids"].index(tid)
            ready = unit["available_s"]
            if index:
                prior = self.live[unit["train_ids"][index - 1]]
                if not prior["complete"]:
                    return False, f"{unit['id']}: предыдущий рейс ещё не завершён"
                ready = prior["release"] + unit["turnaround_s"]
            if now < ready or now >= unit["unavailable_s"]:
                return False, f"{unit['id']}: вне окна доступности"
        return True, ""

    def station_owners(self, station, track, except_id=None):
        owners = []
        now = self.state["sim_time_s"]
        for tid, r in self.live.items():
            if tid == except_id or not r["admitted"]:
                continue
            train = self.trains[tid]
            if r["moving"]:
                m = r["move"]
                if m["destination"] == station and r["arrival_track"] == track:
                    owners.append(tid)  # receiving track reserved before admission
                elif (
                    m["origin"] == station and r["track"] == track and r["x"] <= train.length_m + 25
                ):
                    owners.append(tid)
            elif (
                train.route[min(r["leg"], len(train.route) - 1)] == station
                and r["track"] == track
                and (not r["complete"] or now < r["release"])
            ):
                owners.append(tid)
        return owners

    def footprints(self):
        now = self.state["sim_time_s"]
        for tid, train in self.live.items():
            if train["moving"]:
                yield tid, train
            for move in reversed(train["actual_moves"]):
                if now >= move["release_s"]:
                    break
                yield (
                    tid,
                    dict(
                        move=move,
                        moving=False,
                        x=self.sections[move["section_id"]].length_m,
                        tail_until=move["release_s"],
                    ),
                )

    def track_users(self, section, track, except_id):
        for tid, train in self.footprints():
            move = train["move"]
            if (
                tid != except_id
                and move["section_id"] == section
                and move["main_track_id"] == track
            ):
                yield tid, train

    def departure(self, tid):
        r, train = self.live[tid], self.trains[tid]
        now = self.state["sim_time_s"]
        if r["complete"] or r["moving"] or r["leg"] >= len(self.moves[tid]):
            return
        m = self.moves[tid][r["leg"]]
        origin = self.stops[tid, m["origin"]]
        if not r["admitted"]:
            ok, reason = self.roster_ready(tid)
            if not ok:
                self.hold(tid, reason)
                return
            if now < origin["arrival_s"] or self.station_owners(
                m["origin"], origin["track_id"], tid
            ):
                return
            if self.blocked(f"track:{m['origin']}:{origin['track_id']}"):
                return
            r.update(admitted=True, track=origin["track_id"], ready=now + train.min_dwell_s)
            r["actual_stops"].append(dict(origin, arrival_s=now, departure_s=None))
        if now < max(m["start_s"], r["ready"], train.not_before_s.get(m["origin"], 0)):
            return
        if self.state["awaiting_plan"] or self.replanning:
            self.hold(tid, "Ожидание пересчёта будущих маршрутов")
            return
        if self.state.get("control_mode") == "manual" and clearance_key(
            self.state["active_plan"]["id"], m
        ) not in self.state.get("route_clearances", []):
            self.hold(tid, "Диспетчер должен разрешить отправление")
            return
        ok, reason = self.roster_ready(tid)
        if not ok:
            self.hold(tid, reason)
            self.state["awaiting_plan"] = True
            return
        section = self.sections[m["section_id"]]
        arrival_track = self.stops[tid, m["destination"]]["track_id"]
        targets = [
            f"section:{section.id}",
            f"main_track:{section.id}:{m['main_track_id']}",
            f"track:{m['destination']}:{arrival_track}",
            *[
                f"switch:{sid}:{self.stations[sid]['switch']['id']}"
                for sid in (m["origin"], m["destination"])
                if self.stations[sid].get("switch")
            ],
            *section.shared_resources,
        ]
        if any(self.blocked(k) for k in targets):
            self.hold(tid, "Маршрут закрыт ограничением")
            return
        throat = self.state["execution"]["throats"].get(m["origin"])
        if throat and throat["owner"] != tid:
            self.hold(tid, "Горловина заблокирована другим маршрутом", [throat["owner"]])
            return
        owners = self.station_owners(m["destination"], arrival_track, tid)
        for other, peer in self.track_users(section.id, m["main_track_id"], tid):
            if (
                peer["move"]["origin"] != m["origin"]
                or not section.block_length_m
                or peer["x"] - self.trains[other].length_m - 25
                < min(section.block_length_m, section.length_m)
            ):
                owners.append(other)
        # Shared junctions and station throats remain locked until the tail clears.
        for other, peer in self.footprints():
            if other == tid or not peer["move"]:
                continue
            pm = peer["move"]
            if peer["moving"]:
                ps = self.sections[pm["section_id"]]
                if set(section.shared_resources) & set(ps.shared_resources):
                    owners.append(other)
                if (
                    pm["origin"] == m["origin"]
                    and peer["x"] < self.trains[other].length_m + 250
                    or pm["destination"] == m["origin"]
                    and ps.length_m - peer["x"] < self.trains[other].length_m + 250
                ):
                    owners.append(other)
            elif pm["destination"] == m["origin"] and now < peer["tail_until"]:
                owners.append(other)
        if owners:
            self.hold(tid, "Уступает: занят путь, горловина или приёмный путь", sorted(set(owners)))
            return
        r.update(
            moving=True,
            x=0.0,
            v=0.0,
            move=copy.deepcopy(m),
            arrival_track=arrival_track,
            start=now,
            hold_until=now + train.section_hold_s.get(section.id, 0),
            authority=0.0,
            blockers=[],
            reason="Маршрут разрешён",
            waiting=0.0,
            tail_until=math.inf,
            entry_limit=section_at_entry(train, section, int(now)).max_speed_mps,
        )
        self.state["execution"]["throats"][m["origin"]] = {"owner": tid, "kind": "departure"}
        r["actual_stops"][-1]["departure_s"] = now
        self.event(
            "train.departed",
            tid,
            f"Отправление: {m['origin']} → {m['destination']}, путь {m['main_track_id']}",
        )

    def authority(self, tid):
        r = self.live[tid]
        m = r["move"]
        section = self.sections[m["section_id"]]
        authority = max(0.0, section.length_m - 250)
        owners = []
        locks = self.state["execution"]["throats"]
        target_lock = locks.get(m["destination"])
        horizon = (
            r["v"] ** 2 / (2 * self.trains[tid].braking_mps2)
            + self.trains[tid].max_speed_mps * 2
            + 500
        )
        if section.length_m - r["x"] <= horizon:
            if target_lock is None or target_lock["owner"] == tid:
                switch = self.stations[m["destination"]].get("switch")
                blocked = switch and self.blocked(f"switch:{m['destination']}:{switch['id']}")
                if not blocked:
                    locks[m["destination"]] = {"owner": tid, "kind": "arrival"}
                    authority = section.length_m
            else:
                owners = [target_lock["owner"]]
        if target_lock and target_lock["owner"] == tid:
            authority = section.length_m
        width = section.length_m / max(
            1, math.ceil(section.length_m / (section.block_length_m or section.length_m))
        )
        for other, peer in self.track_users(section.id, m["main_track_id"], tid):
            if peer["move"]["origin"] == m["origin"] and peer["x"] > r["x"]:
                rear = peer["x"] - self.trains[other].length_m - 25
                boundary = max(0.0, math.floor(max(0.0, rear) / width) * width - 25)
                if boundary < authority:
                    authority, owners = boundary, [other]
        # Existing grants cannot be revoked; a sudden failure triggers braking.
        r["authority"] = max(r["authority"], authority)
        r["blockers"] = owners
        return r["authority"]

    def movement(self, tid, dt):
        r, train = self.live[tid], self.trains[tid]
        if not r["moving"]:
            return
        m = r["move"]
        section = self.sections[m["section_id"]]
        authority = self.authority(tid)
        cap = min(
            train.max_speed_mps,
            r["entry_limit"],
            self.cruise.get(f"{tid}:{section.id}", train.max_speed_mps),
        )
        if section.curve_radius_m:
            cap = min(
                cap, math.sqrt(9.81 * section.curve_radius_m * (section.cant_mm + 100) / 1520)
            )
        reverse = m["origin"] == section.station_b
        for limit in section.speed_limits:
            a, b = (
                (section.length_m - limit.end_m, section.length_m - limit.start_m)
                if reverse
                else (limit.start_m, limit.end_m)
            )
            if r["x"] - train.length_m <= b:
                cap = min(
                    cap,
                    math.sqrt(
                        limit.speed_mps**2
                        + 2 * train.braking_mps2 * max(0.0, a - r["x"] - cap * 0.2)
                    ),
                )
        r["current_limit"] = cap
        old = r["v"]
        if train.traction_power_w and old > 0.1:
            acceleration = min(
                train.acceleration_mps2,
                train.traction_power_w * train.traction_efficiency / (train.mass_kg * old),
            )
        else:
            acceleration = train.acceleration_mps2
        target = min(
            cap,
            max(
                0.0,
                math.sqrt(2 * train.braking_mps2 * max(0.0, authority - r["x"]))
                - train.braking_mps2 * 0.2,
            ),
        )
        emergency = (
            self.state["sim_time_s"] < r["hold_until"]
            or self.blocked(f"section:{section.id}")
            or self.blocked(f"main_track:{section.id}:{m['main_track_id']}")
        )
        if emergency:
            target = 0.0
        x, speed = integrate(r["x"], old, target, authority, acceleration, train.braking_mps2, dt)
        dx = x - r["x"]
        mean2 = (old * old + speed * speed) / 2
        if train.davis_resistance:
            c = train.davis_resistance
            resistance = (
                train.mass_kg
                * 9.81
                / 1000
                * (c.a + c.b * (old + speed) * 1.8 + c.c * mean2 * 3.6**2)
            )
        else:
            resistance = (
                train.mass_kg * 9.81 * train.rolling_coefficient + train.drag_n_per_mps2 * mean2
            )
        resistance += train.mass_kg * 9.81 * section.grade_permille / 1000 * (-1 if reverse else 1)
        work = 0.5 * train.mass_kg * (speed * speed - old * old) + resistance * dx
        gross = max(0.0, work) / train.traction_efficiency / 3_600_000
        recovered = (
            max(0.0, -work) * train.regenerative_efficiency * train.grid_receptivity / 3_600_000
        )
        if train.regenerative_power_w:
            recovered = min(recovered, train.regenerative_power_w * dt / 3_600_000)
        recovered = min(recovered, r["gross"] + gross - r["recovered"])
        r["gross"] += gross
        r["recovered"] += recovered
        r["energy"] += gross - recovered
        r["distance"] += dx
        r.update(x=x, v=speed)
        if speed < 0.01:
            r["waiting"] += dt
            if r["blockers"]:
                self.hold(tid, "Ожидание освобождения блок-участка хвостом", r["blockers"])
        if (
            authority - x < 0.05
            and old <= train.braking_mps2 * dt + 1e-8
            and authority >= section.length_m
        ):
            r.update(
                x=section.length_m,
                v=0.0,
                moving=False,
                leg=r["leg"] + 1,
                arrival=self.state["sim_time_s"],
                track=r["arrival_track"],
                ready=self.state["sim_time_s"] + train.min_dwell_s,
                tail_until=self.state["sim_time_s"] + clearance_s(train, section),
            )
            r["actual_moves"].append(
                dict(m, start_s=r["start"], end_s=r["arrival"], release_s=r["tail_until"])
            )
            stop = self.stops[tid, m["destination"]]
            r["actual_stops"].append(dict(stop, arrival_s=r["arrival"], departure_s=None))
            self.event("train.arrived", tid, f"Прибытие: {m['destination']}, путь {r['track']}")
            if r["leg"] == len(self.moves[tid]):
                r.update(complete=True, release=max(r["ready"], r["tail_until"]))
                r["actual_stops"][-1]["departure_s"] = r["release"]
            if abs(r["arrival"] - m["end_s"]) > 5 and not r["complete"]:
                self.state["awaiting_plan"] = True

    def ready_to_plan(self):
        return not any(r["moving"] for r in self.live.values())

    def tick(self, seconds=1):
        if not self.state["running"] or self.replanning or self.state["execution"].get("failed"):
            return
        self._refresh()
        self._ensure_trains()
        if self.state["awaiting_plan"] and self.ready_to_plan():
            return
        left = seconds
        for r in self.live.values():
            r["frames"] = [self.pose(r)]
        while left > 1e-8:
            dt = min(0.2, left)
            left -= dt
            self.state["sim_time_s"] = round(self.state["sim_time_s"] + dt, 9)
            for station, lock in list(self.state["execution"]["throats"].items()):
                owner = self.live[lock["owner"]]
                if (
                    lock["kind"] == "departure"
                    and owner["x"] > self.trains[lock["owner"]].length_m + 250
                    or lock["kind"] == "arrival"
                    and not owner["moving"]
                    and self.state["sim_time_s"] >= owner.get("tail_until", 0)
                ):
                    del self.state["execution"]["throats"][station]
            order = sorted(
                self.trains,
                key=lambda tid: (
                    -int(self.live[tid]["waiting"] >= 300),
                    -self.trains[tid].priority,
                    -self.live[tid]["waiting"],
                    tid,
                ),
            )
            for tid in order:
                self.departure(tid)
            for tid in order:
                r = self.live[tid]
                powered = r["admitted"] or (
                    self.state["sim_time_s"] >= self.trains[tid].release_s
                    and self.roster_ready(tid)[0]
                )
                if powered and (not r["complete"] or self.state["sim_time_s"] < r["release"]):
                    r["energy"] += self.trains[tid].auxiliary_power_w * dt / 3_600_000
                self.movement(tid, dt)
                if (
                    not r["moving"]
                    and not r["complete"]
                    and self.state["sim_time_s"]
                    >= max(r["ready"], self.moves[tid][r["leg"]]["start_s"])
                ):
                    r["waiting"] += dt
                r["frames"].append(self.pose(r))
            from .execution_signals import occupancy

            _, errors = occupancy(self)
            if errors:
                self.state["execution"]["conflicts"] = errors
                self.state["execution"]["failed"] = True
                self.state["running"] = False
                self.event("execution.safety_stop", "", str(errors))
                break
            if self.state["awaiting_plan"] and self.ready_to_plan():
                break
        self.state["state_version"] += 1
        self.state["execution"]["revision"] += 1
        if all(
            r["complete"] and self.state["sim_time_s"] >= r["release"] for r in self.live.values()
        ):
            self.state["running"] = False

    def pose(self, r):
        return dict(
            time_s=self.state["sim_time_s"],
            leg=r["leg"],
            x=r["x"],
            speed_mps=r["v"],
            limit_mps=r.get("current_limit", 0) if r["moving"] else 0,
            energy_kwh=r["energy"],
            distance_travelled_m=r["distance"],
            moving=r["moving"],
            admitted=r["admitted"],
            track=r["track"],
            move=r["move"],
            arrival_track=r.get("arrival_track"),
        )

    def snapshot(self):
        self._refresh()
        self._ensure_trains()
        state = self.state
        result = {
            k: state[k]
            for k in (
                "sim_time_s",
                "state_version",
                "epoch",
                "running",
                "speed",
                "incidents",
                "awaiting_plan",
            )
        }
        result.update(
            engine="logic",
            control_mode=state.get("control_mode", "manual"),
            replanning=self.replanning,
            decision_hold=state["awaiting_plan"] or self.replanning,
            planning_budget_s=state.get(
                "planning_budget_s", 15 if state["scenario"]["metadata"].get("network") else 5
            ),
            traffic=state["scenario"]["metadata"].get("traffic", {}),
            station_capacity=state["scenario"]["metadata"].get("station_capacity", {}),
            track_wear=state["scenario"]["metadata"].get("track_wear", {}),
            active_plan_id=state["active_plan"]["id"],
            plan=self.active_public_plan(),
            trains=copy.deepcopy(state["fleet"]),
            sections=[
                dict(
                    id=s.id,
                    status="open",
                    occupying=[],
                    signal="red",
                    tracks=[
                        dict(id=t.id, status="open", signal="red", occupying=[])
                        for t in s.main_tracks
                    ],
                )
                for s in self.sections.values()
            ],
            stations=[
                dict(
                    id=s["id"],
                    tracks=[
                        dict(t, status="open", occupying=[], blocked_by=[]) for t in s["tracks"]
                    ],
                )
                for s in self.stations.values()
            ],
            switches=[
                dict(
                    id=f"switch:{s['id']}:{s['switch']['id']}",
                    station_id=s["id"],
                    position="normal",
                    available=True,
                    status="free",
                    synthetic=True,
                    train_id=None,
                )
                for s in self.stations.values()
                if s.get("switch")
            ],
            signals=[
                dict(
                    id=f"exit:{s.id}:{t.id}:{origin}",
                    type="exit",
                    station_id=origin,
                    section_id=s.id,
                    main_track_id=t.id,
                    aspect="red",
                    train_id=None,
                    synthetic=True,
                    reason="Ожидание разрешения",
                )
                for s in self.sections.values()
                for t in s.main_tracks
                for origin in (s.station_a, s.station_b)
                if t.direction in ("both", "a_to_b" if origin == s.station_a else "b_to_a")
            ],
        )
        now = self.state["sim_time_s"]
        for t in result["trains"]:
            r = self.live[t["id"]]
            native = self.trains[t["id"]]
            from .traffic_control import CATEGORIES

            category = native.dispatch_category or native.kind
            t.update(
                priority=native.priority,
                dispatch_category=category,
                category_label=CATEGORIES[category][0],
            )
            station = native.route[min(r["leg"], len(native.route) - 1)]
            t.update(
                speed_mps=r["v"],
                energy_kwh=r["energy"],
                distance_travelled_m=r["distance"],
                status="completed" if r["complete"] else "moving" if r["moving"] else "waiting",
                station_id=None if r["moving"] else station,
                station_track_id=r["track"],
                section_id=r["move"]["section_id"] if r["moving"] else None,
                main_track_id=r["move"]["main_track_id"] if r["moving"] else None,
                position_m=self._position(station),
                on_network=r["admitted"] and (not r["complete"] or now < r["release"]),
                holding=r["admitted"] and r["v"] < 0.01 and not r["complete"],
                arrival_s=r["arrival"] if r["complete"] else None,
                delay_s=max(0.0, (r["arrival"] if r["complete"] else now) - native.due_s),
                next_leg=r["leg"],
                eta_s=r["arrival"]
                if r["complete"]
                else None
                if state["awaiting_plan"]
                else self.moves[t["id"]][-1]["end_s"],
                yield_to=r["blockers"],
                yield_reason=r["reason"],
                waiting_for=r["blockers"],
                wait_reason=r["reason"],
                execution_frames=r["frames"],
                actual_authority_m=r["authority"],
                gross_energy_kwh=r["gross"],
                recovered_energy_kwh=r["recovered"],
            )
            if r["moving"]:
                m = r["move"]
                section = self.sections[m["section_id"]]
                t.update(
                    position_m=self._position(m["origin"])
                    + (self._position(m["destination"]) - self._position(m["origin"]))
                    * r["x"]
                    / section.length_m,
                    direction=1 if m["origin"] == section.station_a else -1,
                    departure_track_id=r["track"],
                    arrival_track_id=r["arrival_track"],
                )
        for station in result["stations"]:
            for track in station["tracks"]:
                track["occupying"] = self.station_owners(station["id"], track["id"])
                track["status"] = (
                    "closed"
                    if self.blocked(f"track:{station['id']}:{track['id']}")
                    else "occupied"
                    if track["occupying"]
                    else "open"
                )
        for section in result["sections"]:
            active = [
                tid
                for tid, r in self.live.items()
                if r["move"]
                and r["move"]["section_id"] == section["id"]
                and (r["moving"] or now < r["tail_until"])
            ]
            section.update(
                occupying=active,
                signal="red" if active or self.blocked("section:" + section["id"]) else "green",
            )
        from .execution_signals import observations

        observations(self, result)
        result["metrics"] = actual_metrics(
            self.state, result["trains"], config_for(self.state), self.plan_violations()
        )
        result["dispatch_events"] = self.state["execution"]["events"]
        result["execution_model"] = "unified-fixed-step"
        result["execution_planning_boundary"] = self.ready_to_plan()
        result["pending_departures"] = [
            dict(
                self.moves[tid][r["leg"]],
                authorized=self.state.get("control_mode") == "automatic"
                or clearance_key(self.state["active_plan"]["id"], self.moves[tid][r["leg"]])
                in self.state.get("route_clearances", []),
            )
            for tid, r in self.live.items()
            if not r["moving"] and not r["complete"]
        ]
        result["manual_hold"] = any(not m["authorized"] for m in result["pending_departures"])
        return result

    def plan_violations(self):
        if self.state.get("execution") is None:
            return super().plan_violations()
        if self.state["awaiting_plan"]:
            return [
                {
                    "code": "ACTUAL_REPLAN",
                    "message": "Исполнение изменилось; прогноз требует пересчёта",
                }
            ]
        return list(self.state["execution"]["conflicts"])

    def profile(self, train_id):
        if train_id not in self.live:
            raise KeyError(train_id)
        r = self.live[train_id]
        return dict(
            train_id=train_id,
            plan_id=self.state["active_plan"]["id"],
            points=[
                [
                    p["time_s"],
                    p["distance_travelled_m"],
                    p["speed_mps"],
                    p["limit_mps"],
                    p["energy_kwh"],
                ]
                for p in r["frames"]
            ],
            energy_kwh=r["energy"],
            arrival_s=r["arrival"] if r["complete"] else None,
            reachable=not self.state["awaiting_plan"],
            applicable=not self.state["awaiting_plan"],
            violations=self.plan_violations(),
            assumptions=[
                "Фактическое пошаговое движение; энергия: тяга, сопротивление, уклон, рекуперация и вспомогательная мощность."
            ],
        )

    def install_plan(self, plan):
        if not self.ready_to_plan():
            raise ValueError("Entered movements must finish before changing the plan")
        plan["_profiles"] = dict(self.state["active_plan"].get("_profiles", {})) | plan.get(
            "_profiles", {}
        )
        self.state.update(active_plan=plan, awaiting_plan=False, route_clearances=[])
        self.state["state_version"] += 1
        self._cache_key = None
        self._refresh()
        for tid, r in self.live.items():
            if r["complete"]:
                continue
            # A fresh plan may extend dwell, but never replaces observed arrival.
            stop = self.stops[tid, self.trains[tid].route[r["leg"]]]
            r["ready"] = max(r["ready"], stop["departure_s"])
        self.event(
            "plan.execution_updated",
            "",
            "Пересчитаны будущие маршруты; фактические позиции сохранены",
        )

    def load_case(self, kind):
        super().load_case(kind)
        target = self.state["sim_time_s"]
        changed = copy.deepcopy(self.state["scenario"])
        self.state.update(
            scenario=copy.deepcopy(self.state["baseline_scenario"]),
            sim_time_s=0.0,
            awaiting_plan=False,
            running=True,
            control_mode="automatic",
        )
        self._cache_key = None
        self.tick(target)
        self.state.update(
            scenario=changed, awaiting_plan=True, running=False, control_mode="manual"
        )
        self._cache_key = None
        self._refresh()
        self.event("scenario.loaded", "", "Сбой применён к фактически исполненному участку графика")
