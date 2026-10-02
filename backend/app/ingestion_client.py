"""Atomic latest-revision ingestion, adapted from M_part to the integrated SI API."""

import asyncio
import copy
import os
import time
import uuid

import httpx

from backend.ingestion.service import normalize


class IngestionClient:
    def __init__(self, deliver, current=None):
        self.deliver = deliver
        self.current = current
        self.url = os.environ.get("INGEST_URL", "").rstrip("/")
        self.queue = asyncio.Queue(maxsize=1)
        self.latest = None
        self.ready = asyncio.Event()
        self.status = {
            "mode": "service" if self.url else "embedded",
            "accepted": 0,
            "dropped": 0,
            "error": None,
        }
        self.task = asyncio.create_task(self.run()) if self.url else None

    def accept(self, snapshot, observed_at):
        if self.current and (snapshot["epoch"], snapshot["state_version"]) != self.current():
            self.status["dropped"] += 1
            return
        self.latest = snapshot
        self.status.update(
            accepted=self.status["accepted"] + 1, last_success_at=time.monotonic(), error=None
        )
        self.deliver(snapshot, observed_at)
        self.ready.set()

    def publish(self, snapshot, observed_at=None):
        observed_at = observed_at or time.perf_counter()
        snapshot = normalize(snapshot)
        if not self.url:
            self.accept(snapshot, observed_at)
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.status["dropped"] += 1
        self.queue.put_nowait(({"event_id": str(uuid.uuid4()), "payload": snapshot}, observed_at))

    async def current_snapshot(self):
        async with asyncio.timeout(2):
            while True:
                self.ready.clear()
                if (
                    self.latest
                    and (
                        not self.current
                        or (self.latest["epoch"], self.latest["state_version"]) == self.current()
                    )
                    and time.monotonic() - self.status.get("last_success_at", 0) < 5
                ):
                    return copy.deepcopy(self.latest)
                await self.ready.wait()

    async def run(self):
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            while True:
                packet, observed_at = await self.queue.get()
                try:
                    response = await client.post(
                        self.url + "/events",
                        json=packet,
                        headers={"Authorization": "Bearer " + os.environ.get("INGEST_TOKEN", "")},
                    )
                    response.raise_for_status()
                    snapshot = normalize(response.json()["payload"])
                    if snapshot != packet["payload"]:
                        raise ValueError("Ingestion changed canonical telemetry")
                    self.accept(snapshot, observed_at)
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    self.status["error"] = type(error).__name__

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
