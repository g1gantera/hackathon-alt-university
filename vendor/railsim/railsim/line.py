"""
Модель линии: перегоны с одно-/двухпутным движением, блок-участками,
закрытиями и ограничениями скорости, плюс два диспетчера:
  FifoDispatcher  — базовый: кто первым запросил, тот первым поехал;
  SmartDispatcher — автодиспетчер: приоритеты, пачки попутных поездов,
                    ограничение пачки, чтобы встречные не ждали бесконечно.
"""
from dataclasses import dataclass

import simpy

from .util import EPS


@dataclass(eq=False)
class Request:
    train: object
    direction: int
    t_request: float
    event: simpy.Event


class Segment:
    """Перегон между двумя соседними станциями."""

    def __init__(self, env, idx, st_a, st_b, spec, cfg, dispatcher):
        self.env = env
        self.idx = idx
        self.name = f"{st_a['name']} – {st_b['name']}"
        self.km_a = float(st_a["km"])
        self.km_b = float(st_b["km"])
        self.length_km = abs(self.km_b - self.km_a)
        self.spec = spec
        self.tracks = int(spec["tracks"])
        self.max_speed = float(spec["max_speed_kmh"])
        self.grade = float(spec["grade_permille"])
        self.electrified = bool(spec["electrified"])
        # Автоблокировка: несколько попутных поездов на перегоне (по числу блок-участков).
        # Полуавтоблокировка: между станциями только один поезд.
        if spec["blocking"] == "auto":
            self.blocks = max(1, int(self.length_km // float(cfg.BLOCK_SECTION_KM)))
        else:
            self.blocks = 1
        self.dispatcher = dispatcher
        self.occ = {1: 0, -1: 0}       # поездов на перегоне по направлениям
        self.queue = []                # ожидающие запросы
        self.closures = []             # (начало, конец, причина)
        self.restrictions = []         # (начало, конец, доля скорости, причина)
        self.hazard_windows = []       # необнаруженные угрозы: (начало, конец, p_схода, вид, id)
        self.platoon_dir = None
        self.platoon_count = 0

    # ── состояние ───────────────────────────────────────────────────────────
    def is_closed(self, t: float) -> bool:
        return any(s <= t < e for s, e, _ in self.closures)

    def speed_factor(self, t: float) -> float:
        active = [f for s, e, f, _ in self.restrictions if s <= t < e]
        return min(active) if active else 1.0

    def can_enter(self, direction: int) -> bool:
        if self.is_closed(self.env.now):
            return False
        if self.tracks >= 2:
            return self.occ[direction] < self.blocks
        return self.occ[-direction] == 0 and self.occ[direction] < self.blocks

    # ── закрытия и ограничения ──────────────────────────────────────────────
    def add_closure(self, start: float, end: float, reason: str):
        if end <= start:
            return
        self.closures.append((start, end, reason))
        self.env.process(self._wake_at(end + EPS))

    def add_restriction(self, start: float, end: float, factor: float, reason: str):
        if end > start:
            self.restrictions.append((start, end, float(factor), reason))

    def _wake_at(self, t: float):
        if t > self.env.now:
            yield self.env.timeout(t - self.env.now)
        self._try_grant()

    # ── занятие перегона ────────────────────────────────────────────────────
    def request(self, train, direction: int) -> simpy.Event:
        ev = self.env.event()
        self.queue.append(Request(train, direction, self.env.now, ev))
        self._try_grant()
        return ev

    def release(self, direction: int):
        self.occ[direction] -= 1
        self._try_grant()

    def _occupy(self, direction: int):
        self.occ[direction] += 1
        if self.tracks == 1:
            if self.platoon_dir == direction:
                self.platoon_count += 1
            else:
                self.platoon_dir, self.platoon_count = direction, 1

    def _try_grant(self):
        if not self.queue or self.is_closed(self.env.now):
            return
        now = self.env.now
        ordered = self.dispatcher.order(self, self.queue, now)
        granted, blocked = [], set()
        for req in ordered:
            key = 0 if self.tracks == 1 else req.direction
            if self.dispatcher.strict_fifo and key in blocked:
                continue
            if self.can_enter(req.direction) and self.dispatcher.allow(self, req, self.queue):
                self._occupy(req.direction)
                granted.append(req)
            else:
                blocked.add(key)
        for req in granted:
            self.queue.remove(req)
            req.event.succeed()


class FifoDispatcher:
    """Базовый сценарий: строгая очередь по времени запроса."""
    strict_fifo = True

    def order(self, seg, queue, now):
        return sorted(queue, key=lambda r: r.t_request)

    def allow(self, seg, req, queue):
        return True


class SmartDispatcher:
    """
    Автодиспетчер. Оценка запроса:
      score = w_priority*приоритет + w_delay*накопленная_задержка
              + w_mass*масса(тыс. т) + w_wait*текущее_ожидание
    На однопутке сначала пропускается направление с большей суммарной оценкой
    (пачка попутных поездов вместо постоянной смены направления), но не больше
    AUTO_MAX_PLATOON поездов подряд, если ждут встречные.
    """
    strict_fifo = False

    def __init__(self, cfg):
        w = cfg.AUTO_WEIGHTS
        self.w_priority = float(w["priority"])
        self.w_delay = float(w["delay"])
        self.w_mass = float(w["mass"])
        self.w_wait = float(w["wait"])
        self.max_platoon = int(cfg.AUTO_MAX_PLATOON)

    def score(self, req, now):
        tr = req.train
        return (self.w_priority * tr.priority
                + self.w_delay * tr.delay_so_far
                + self.w_mass * tr.mass_t / 1000.0
                + self.w_wait * (now - req.t_request))

    def order(self, seg, queue, now):
        scores = {id(r): self.score(r, now) for r in queue}
        if seg.tracks == 1:
            dir_score = {1: 0.0, -1: 0.0}
            for r in queue:
                dir_score[r.direction] += scores[id(r)]
            return sorted(queue, key=lambda r: (-dir_score[r.direction], -scores[id(r)]))
        return sorted(queue, key=lambda r: -scores[id(r)])

    def allow(self, seg, req, queue):
        if seg.tracks != 1:
            return True
        if seg.platoon_dir == req.direction and seg.platoon_count >= self.max_platoon:
            return not any(r.direction == -req.direction for r in queue)
        return True
