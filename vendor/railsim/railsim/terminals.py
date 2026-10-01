"""Погранпереход (перегрузка/смена колеи, таможня) и порт с паромом."""
import simpy

from .util import crn_uniform, pick


class Border:
    def __init__(self, env, cfg, scenario, seed):
        self.env = env
        self.cfg = cfg
        self.scenario = scenario
        self.seed = seed
        self.res = simpy.Resource(env, capacity=int(cfg.BORDER_TRACKS))

    def expected_h(self):
        return float(self.cfg.BORDER_GAUGE_CHANGE_H) + float(
            pick(self.cfg.BORDER_CUSTOMS_H, self.scenario, "border_preannounce"))

    def expected_total_h(self):
        """Ожидаемое время на переходе с учётом доли поездов без предварительной информации."""
        pre = float(pick(self.cfg.BORDER_PREANNOUNCE_PROB, self.scenario, "border_preannounce"))
        return self.expected_h() + (1 - pre) * float(self.cfg.BORDER_PREP_H)

    def process(self, tr):
        cfg = self.cfg
        t0 = self.env.now
        with self.res.request() as req:
            yield req
            pre_prob = float(pick(cfg.BORDER_PREANNOUNCE_PROB, self.scenario, "border_preannounce"))
            announced = crn_uniform(self.seed, "border", tr.id) < pre_prob
            t = self.expected_h() + (0.0 if announced else float(cfg.BORDER_PREP_H))
            yield self.env.timeout(t)
        tr.border_h += self.env.now - t0


def generate_ferries(cfg, rng, horizon_h):
    """Расписание паромов: (плановое отправление, фактическое) с учётом задержек."""
    out, t, last_actual = [], float(cfg.FERRY_FIRST_DEPARTURE_H), 0.0
    while t <= horizon_h:
        delay = 0.0
        if rng.random() < float(cfg.FERRY_DELAY_PROB):
            delay = float(rng.exponential(float(cfg.FERRY_DELAY_MEAN_H)))
        actual = max(t + delay, last_actual)
        out.append((t, actual))
        last_actual = actual
        t += float(cfg.FERRY_INTERVAL_H)
    return out


class Port:
    def __init__(self, env, cfg, scenario, ferries):
        self.env = env
        self.cfg = cfg
        self.scenario = scenario
        self.ferries = ferries
        self.cap = int(cfg.FERRY_CAPACITY_TRAINS)
        self.bookings = [0] * len(ferries)
        self.yard = simpy.Resource(env, capacity=int(cfg.PORT_YARD_TRACKS))
        self.ready = []  # (поезд, событие, время готовности)
        self.learned_delay_h = 0.0  # оценка типичной задержки в пути (обучается по факту)
        env.process(self._ferry_loop())

    def record_line_delay(self, delay_h):
        """Автодиспетчер уточняет прогноз прибытия по фактическим задержкам (скользящее среднее)."""
        a = float(self.cfg.SLOT_DELAY_LEARNING)
        self.learned_delay_h = (1 - a) * self.learned_delay_h + a * delay_h

    def book(self, tr, eta):
        """Бронирование слота: возвращает, на сколько часов придержать поезд на отправлении."""
        cfg = self.cfg
        ready_at = eta + self.learned_delay_h + float(cfg.PORT_HANDLING_H)
        for i, (sched, _) in enumerate(self.ferries):
            if sched >= ready_at and self.bookings[i] < self.cap:
                self.bookings[i] += 1
                tr.booked_ferry = i
                gap = sched - ready_at
                if gap > float(cfg.SLOT_HOLD_THRESHOLD_H):
                    return max(0.0, gap - float(cfg.SLOT_BUFFER_H))
                return 0.0
        return 0.0

    def arrive(self, tr):
        t0 = self.env.now
        req = self.yard.request()
        yield req
        tr.port_yard_queue_h = self.env.now - t0
        yield self.env.timeout(float(self.cfg.PORT_HANDLING_H))
        ev = self.env.event()
        self.ready.append((tr, ev, self.env.now))
        t1 = self.env.now
        yield ev
        tr.port_wait_h = self.env.now - t1
        self.yard.release(req)

    def _ferry_loop(self):
        for i, (_, actual) in enumerate(self.ferries):
            if actual > self.env.now:
                yield self.env.timeout(actual - self.env.now)
            if self.scenario.on("port_slots"):
                cand = sorted(self.ready, key=lambda x: (0 if x[0].booked_ferry == i else 1, x[2]))
            else:
                cand = sorted(self.ready, key=lambda x: x[2])
            for item in cand[:self.cap]:
                self.ready.remove(item)
                item[0].ferry_index = i
                item[1].succeed()
