"""SI normalization and bounded in-memory event bus (Kafka imitation for the demo)."""

import os
import secrets
from collections import deque
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Flexible(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class TrainTelemetry(Flexible):
    id: str
    position_m: float = Field(ge=0)
    speed_mps: float = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def si(cls, value):
        value = dict(value)
        if "speed_mps" not in value and "speed_kmh" in value:
            value["speed_mps"] = value.pop("speed_kmh") / 3.6
        return value


class SignalTelemetry(Flexible):
    id: str
    signal: Literal["red", "green", "yellow"]


class SwitchTelemetry(Flexible):
    station_id: str
    position: Literal["normal", "reverse", "route"]
    available: bool


class MovementTelemetry(Flexible):
    train_id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)

    @model_validator(mode="after")
    def chronological(self):
        if self.end_s <= self.start_s:
            raise ValueError("Movement must end after it starts")
        return self


class Timetable(Flexible):
    movements: list[MovementTelemetry]


class SnapshotTelemetry(Flexible):
    epoch: str
    state_version: int = Field(ge=0)
    sim_time_s: float = Field(ge=0)
    trains: list[TrainTelemetry] = Field(min_length=1, max_length=500)
    sections: list[SignalTelemetry]
    switches: list[SwitchTelemetry]
    plan: Timetable


def normalize(payload):
    result = SnapshotTelemetry.model_validate(payload).model_dump()
    for move in result["plan"]["movements"]:
        if move["end_s"] <= move["start_s"]:
            raise ValueError("Timetable movement must end after it starts")
    if len({t["id"] for t in result["trains"]}) != len(result["trains"]):
        raise ValueError("Duplicate train telemetry")
    return result


class Packet(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    payload: SnapshotTelemetry


class EventBus:
    def __init__(self, capacity=256):
        self.records = deque(maxlen=capacity)
        self.offset = 0

    def publish(self, event_id, payload):
        prior = next((r for r in self.records if r["event_id"] == event_id), None)
        if prior:
            if prior["payload"] != payload:
                raise ValueError("Event ID already exists with a different payload")
            return prior
        self.offset += 1
        item = {"offset": self.offset, "event_id": event_id, "payload": payload, "units": "SI"}
        self.records.append(item)
        return item


app = FastAPI(title="RailFlow telemetry ingestion", version="1.0")
bus = EventBus()


def authorized(request):
    token = os.environ.get("INGEST_TOKEN", "")
    if not token or not secrets.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + token
    ):
        raise HTTPException(401, "Internal ingestion token required")


@app.get("/health")
def health():
    return {"ok": True, "bus": "bounded-memory", "retained_events": len(bus.records)}


@app.post("/events")
def ingest(body: Packet, request: Request):
    authorized(request)
    try:
        return bus.publish(body.event_id, normalize(body.payload.model_dump()))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/events")
def events(request: Request, after: int = Query(0, ge=0)):
    authorized(request)
    first = bus.records[0]["offset"] if bus.records else bus.offset + 1
    return {
        "events": [e for e in bus.records if e["offset"] > after],
        "offset": bus.offset,
        "gap": after < first - 1,
    }
