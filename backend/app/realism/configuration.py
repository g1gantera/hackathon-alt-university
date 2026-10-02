"""Translate the corridor to railsim units without claiming calibrated input."""

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

from railsim.validate import find_errors, find_missing

from backend.app.schemas import Scenario

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE = ROOT / "config/railsim.example.json"


def load_profile(path=DEFAULT_PROFILE):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if profile.get("calibration_status") not in ("illustrative", "user_supplied"):
        raise ValueError("Declare the calibration_status of the railsim parameters")
    if not profile.get("note", "").strip():
        raise ValueError("Explain the origin of the realism parameters")
    return profile


def build_config(scenario: Scenario, profile=None, *, runs=None, seed=None):
    if len(scenario.trains) < 5:
        raise ValueError("Corridor realism simulation requires at least 5 trains")
    profile = load_profile() if profile is None else copy.deepcopy(profile)
    values = copy.deepcopy(profile["parameters"])

    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return not isinstance(value, (int, float)) or math.isfinite(value)

    if not finite(profile):
        raise ValueError("Realism parameters must be finite")
    if runs is not None:
        values["N_RUNS"] = runs
    if seed is not None:
        values["RANDOM_SEED"] = seed
    for key in ("SIM_DAYS", "N_RUNS"):
        if type(values[key]) is not int or not 1 <= values[key] <= 100:
            raise ValueError(f"{key} must be an integer in 1..100")
    if type(values["RANDOM_SEED"]) is not int or values["RANDOM_SEED"] < 0:
        raise ValueError("RANDOM_SEED must be a nonnegative integer")
    for key in ("DRAIN_HOURS", "BLOCK_SECTION_KM", "DIESEL_KWH_PER_L"):
        if not math.isfinite(values[key]) or values[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
    # Terminals are not part of this corridor. Their source modules remain available.
    if values["USE_BORDER"] or values["USE_PORT"] or values["USE_CIS_EXCHANGE"]:
        raise ValueError("Border, ferry and international exchange are outside this corridor")
    stations, segments, distance = [], [], 0.0
    for index, station in enumerate(scenario.stations):
        stations.append(
            {
                "name": station.id,
                "km": distance / 1000,
                "passenger_stop": station.id in profile["passenger_stops"],
            }
        )
        if index == len(scenario.stations) - 1:
            continue
        following = scenario.stations[index + 1]
        section = next(
            (
                s
                for s in scenario.sections
                if s.station_a == station.id and s.station_b == following.id
            ),
            None,
        )
        if section is None:
            raise ValueError("Railsim adapter requires an ordered linear corridor")
        if len(section.main_tracks) not in (1, 2):
            raise ValueError("Source railsim supports one or two main tracks")
        spec = {**profile["segment_defaults"], **profile["section_overrides"].get(section.id, {})}
        spec.update(tracks=len(section.main_tracks), max_speed_kmh=section.max_speed_mps * 3.6)
        segments.append(spec)
        distance += section.length_m
    if set(profile["section_overrides"]) - {s.id for s in scenario.sections}:
        raise ValueError("Unknown section in realism overrides")
    values.update(STATIONS=stations, SEGMENTS=segments, TRAIN_TYPES={}, TRAIN_FLOWS=[])
    for train in scenario.trains:
        # Per-train type preserves mass, priority and speed even when the same class differs.
        values["TRAIN_TYPES"][train.id] = {
            "priority": train.priority,
            "mass_t": train.mass_kg / 1000,
            "max_speed_kmh": train.max_speed_mps * 3.6,
            "wagons": profile["wagons"][train.kind],
            "delay_cost_kzt_h": profile["delay_cost_kzt_h"][train.kind],
            "cross_border": False,
            "to_port": False,
            "passenger": train.kind == "passenger",
        }
        if train.release_s >= 86400:
            raise ValueError("Fixed daily departures must be within the first 24 hours")
        values["TRAIN_FLOWS"].append(
            {
                "type": train.id,
                "origin": train.route[0],
                "destination": train.route[-1],
                "trains_per_day": 1,
                "fixed_departure_hours": [train.release_s / 3600],
                "departure_hour_weights": None,
            }
        )
    cfg = SimpleNamespace(**values)
    errors = find_missing(cfg)
    if not errors:
        errors = find_errors(cfg)
    if errors:
        raise ValueError("Invalid railsim configuration: " + "; ".join(errors))
    for key in ("ELECTRIC_EFFICIENCY", "DIESEL_EFFICIENCY"):
        if not 0 < getattr(cfg, key) <= 1:
            raise ValueError(f"{key} must be in (0, 1]")
    for key in (
        "RESISTANCE_A",
        "RESISTANCE_B",
        "RESISTANCE_C",
        "LOCO_RESCUE_H",
        "WAGON_SETOUT_H",
        "DERAIL_CLOSURE_H",
        "MAINT_DURATION_H",
        "LOCO_FAILURE_BASE_PER_1000KM",
        "WAGON_DEFECT_RATE_PER_1000KM",
        "LOCO_WEAR_SENSITIVITY",
        "PEOPLE_INCIDENT_RATE_PER_100KM_YEAR",
        "ELECTRICITY_PRICE_KZT_KWH",
        "DIESEL_PRICE_KZT_L",
    ):
        if getattr(cfg, key) < 0:
            raise ValueError(f"{key} must be nonnegative")
    for kind, hazard in cfg.HAZARDS.items():
        if (
            hazard["rate_per_100km_day"] < 0
            or hazard["mean_duration_h"] <= 0
            or hazard["undetected_discovery_h"] < 0
        ):
            raise ValueError(f"Invalid hazard rate or duration: {kind}")
    for spec in cfg.SEGMENTS:
        if any(
            spec[k] < 0
            for k in (
                "wear",
                "flood_risk",
                "snow_risk",
                "sand_risk",
                "heat_risk",
                "theft_risk",
                "crossings_guarded",
                "crossings_unguarded",
            )
        ):
            raise ValueError("Risk multipliers and crossing counts must be nonnegative")
    return cfg
