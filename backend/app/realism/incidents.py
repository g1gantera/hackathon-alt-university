"""Translate source rail/weather/vehicle problems into independently checked plans."""

import math
from dataclasses import dataclass, field

from railsim.hazards import HAZARD_KINDS, RawHazard, RawIncident, apply_hazards, apply_incidents
from railsim.util import Scenario as SourceScenario

from app.realism.configuration import build_config
from app.schemas import Block, EntrySpeedLimit, Plan, Scenario

REALISM_INCIDENTS = tuple(
    f"railsim_{kind}"
    for kind in (
        *HAZARD_KINDS,
        "loco_failure",
        "wagon_defect",
        "crossing",
        "people",
        "maintenance",
        "derailment",
    )
)

LABELS = {
    "rail_defect": "Обнаруженный дефект рельса",
    "theft": "Хищение деталей пути",
    "flood": "Паводок / размыв пути",
    "snow": "Снежный занос",
    "sand": "Песчаный занос",
    "heat": "Жара / температурное ограничение",
    "loco_failure": "Отказ локомотива и ожидание помощи",
    "wagon_defect": "Выявленный дефект вагона и отцепка",
    "crossing": "Происшествие на переезде",
    "people": "Происшествие с людьми на пути",
    "maintenance": "Плановое ремонтное окно",
    "derailment": "Сход и освобождение пути",
}


@dataclass
class ConstraintRecorder:
    """The unchanged source functions can emit constraints without a SimPy environment."""

    closures: list = field(default_factory=list)
    restrictions: list = field(default_factory=list)
    hazard_windows: list = field(default_factory=list)

    def add_closure(self, start, end, reason):
        if end > start:
            self.closures.append((start, end, reason))

    def add_restriction(self, start, end, factor, reason):
        if end > start:
            self.restrictions.append((start, end, factor, reason))


def apply_recorded_constraints(scenario, recorders, *, prefix):
    for section, recorder in zip(scenario.sections, recorders, strict=True):
        for index, (start, end, reason) in enumerate(recorder.closures):
            lo, hi = (
                max(0, math.floor(start * 3600)),
                min(scenario.horizon_s, math.ceil(end * 3600)),
            )
            if hi > lo:
                scenario.blocks.append(
                    Block(
                        id=f"{prefix}:{section.id}:closure:{index}",
                        resource=f"section:{section.id}",
                        start_s=lo,
                        end_s=hi,
                    )
                )
        for index, (start, end, factor, reason) in enumerate(recorder.restrictions):
            lo, hi = (
                max(0, math.floor(start * 3600)),
                min(scenario.horizon_s, math.ceil(end * 3600)),
            )
            if hi > lo:
                section.entry_speed_limits.append(
                    EntrySpeedLimit(
                        id=f"{prefix}:{section.id}:speed:{index}",
                        start_s=lo,
                        end_s=hi,
                        speed_factor=factor,
                        reason=reason,
                    )
                )


def inject_incident(base: Scenario, previous: Plan, name: str) -> Scenario:
    if name not in REALISM_INCIDENTS:
        raise ValueError(f"Unknown realism incident: {name}")
    kind = name.removeprefix("railsim_")
    cfg = build_config(base)
    scenario = base.model_copy(deep=True)
    first = min(previous.movements, key=lambda m: m.start_s)
    scenario.now_s = first.start_s + max(1, (first.end_s - first.start_s) // 3)
    scenario.state_version += 1
    future = [
        m
        for m in previous.movements
        if m.train_id == first.train_id and m.start_s > scenario.now_s + 1800
    ]
    if not future:
        raise ValueError("A realism incident needs a future movement to preserve history")
    target = next((m for m in future if m.origin == "BURABAY"), future[0])
    index = next(i for i, s in enumerate(scenario.sections) if s.id == target.section_id)
    train = next(t for t in scenario.trains if t.id == target.train_id)
    start_h = (target.start_s - 60) / 3600
    recorders = [ConstraintRecorder() for _ in scenario.sections]
    reaction = "Запрет входа / ожидание освобождения; пересчитать будущие времена"
    details = {
        "kind": name,
        "label": LABELS[kind],
        "detected_s": scenario.now_s,
        "source_module": "railsim.hazards",
        "calibration_status": "illustrative",
        "resource": f"section:{target.section_id}",
        "injection": "controlled_stress_case_with_announced_future_constraint",
    }
    if kind in HAZARD_KINDS:
        p = cfg.HAZARDS[kind]
        # This card is an explicitly detected problem, not a stochastic safety claim.
        detection_cfg = dict(cfg.DETECTION_PROB["baseline"])
        detection_cfg[kind] = 1.0
        cfg.DETECTION_PROB = {"baseline": detection_cfg, "auto": detection_cfg}
        hazard = RawHazard(0, kind, index, start_h, float(p["mean_duration_h"]), 0.0)
        apply_hazards(recorders, [hazard], cfg, SourceScenario("known_problem"))
        details["source_event"] = vars(hazard)
        if not p["closure"]:
            reaction = (
                "Ограничить скорость при входе во время явления, восстановить после его окончания"
            )
    elif kind in ("crossing", "people"):
        incident = RawIncident(kind, index, start_h, 0.0)
        apply_incidents(recorders, [incident], cfg, SourceScenario("known_problem"))
        details["source_event"] = vars(incident)
    elif kind == "maintenance":
        recorders[index].add_closure(start_h, start_h + cfg.MAINT_DURATION_H, kind)
        reaction = "Учесть заранее объявленное ремонтное окно; перенести входы за его границы"
    elif kind in ("loco_failure", "derailment"):
        duration = cfg.LOCO_RESCUE_H if kind == "loco_failure" else cfg.DERAIL_CLOSURE_H
        train.section_hold_s[target.section_id] = math.ceil(duration * 3600)
        details.update(
            source_module="railsim.trains",
            train_id=train.id,
            stationary_hold_s=train.section_hold_s[target.section_id],
        )
        reaction = "Удержать занятый поездом путь на время помощи; пересчитать последующие поезда"
        if kind == "derailment":
            # Recovery blocks other paths relative to the actual new entry time.
            train.section_recovery_all_tracks.append(target.section_id)
            details["approximation"] = (
                "Stationary recovery starts at the entry boundary; derailment geometry is not modeled"
            )
    elif kind == "wagon_defect":
        stop = next(
            s
            for s in previous.stops
            if s.train_id == train.id and s.station_id == target.destination
        )
        train.not_before_s[target.destination] = stop.departure_s + math.ceil(
            cfg.WAGON_SETOUT_H * 3600
        )
        details.update(
            source_module="railsim.trains",
            train_id=train.id,
            station_id=target.destination,
            setout_s=math.ceil(cfg.WAGON_SETOUT_H * 3600),
        )
        reaction = "Отцепка обнаруженного неисправного вагона на станции; сдвиг готовности поезда"
    apply_recorded_constraints(scenario, recorders, prefix=name)
    details["expected_resolution"] = reaction
    details["speed_semantics"] = "factor sampled at section entry, as in source railsim"
    scenario.metadata["incident"] = details
    return Scenario.model_validate(scenario.model_dump())
