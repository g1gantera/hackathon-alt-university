"""Вспомогательные функции: сценарии и общие случайные числа (CRN)."""
import zlib
from dataclasses import dataclass, field

import numpy as np

EPS = 1e-9

# Какая функция автодиспетчера включает "auto"-значение параметра
FEATURES = (
    "smart_dispatch", "speed_advisory", "hazard_monitoring", "predictive_maintenance",
    "wagon_detectors", "crossing_safety", "maintenance_windows", "border_preannounce",
    "port_slots", "energy_optimization",
)


@dataclass
class Scenario:
    name: str
    features: set = field(default_factory=set)

    def on(self, feature: str) -> bool:
        return feature in self.features


def pick(param: dict, scenario: Scenario, feature: str):
    """Значение параметра {"baseline":…, "auto":…} для сценария."""
    return param["auto"] if scenario.on(feature) else param["baseline"]


def _stable_hash(x) -> int:
    return zlib.crc32(str(x).encode("utf-8"))


def crn_uniform(seed: int, *keys) -> float:
    """
    Детерминированное равномерное число U(0,1) по ключам.
    Одинаковые ключи → одинаковое число в любом сценарии, поэтому сценарии
    сравниваются на одних и тех же случайных событиях (common random numbers).
    """
    ss = np.random.SeedSequence([int(seed)] + [_stable_hash(k) for k in keys])
    return float(np.random.default_rng(ss).random())
