"""Asynchronous producer; slow ingestion never blocks planning or HTTP requests."""

import asyncio
import os
import time
import uuid

import httpx

from backend.ingestion.service import normalize


class IngestionClient:
    def __init__(self, deliver):
        self.deliver = deliver
        self.url = os.environ.get("INGEST_URL", "").rstrip("/")
        self.queue = asyncio.Queue(maxsize=8)
        self.status = {
            "mode": "service" if self.url else "embedded",
            "accepted": 0,
            "dropped": 0,
            "error": None,
        }
        self.task = asyncio.create_task(self.run()) if self.url else None

    def publish(self, snapshot, observed_at=None):
        observed_at = observed_at or time.perf_counter()
        if not self.url:
            self.deliver(normalize(snapshot), observed_at)
            self.status["accepted"] += 1
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.status["dropped"] += 1
        self.queue.put_nowait(({"event_id": str(uuid.uuid4()), "payload": snapshot}, observed_at))

    async def run(self):
        async with httpx.AsyncClient(timeout=1) as client:
            while True:
                packet, observed_at = await self.queue.get()
                try:
                    response = await client.post(
                        self.url + "/events",
                        json=packet,
                        headers={"Authorization": "Bearer " + os.environ.get("INGEST_TOKEN", "")},
                    )
                    response.raise_for_status()
                    self.deliver(response.json()["payload"], observed_at)
                    self.status.update(accepted=self.status["accepted"] + 1, error=None)
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    # Retain last visible state; reconnection uses the next full snapshot.
                    self.status["error"] = type(error).__name__

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
