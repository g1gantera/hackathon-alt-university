"""Bounded latest-state event-bus imitation; never combine different revisions."""
import asyncio
import copy
import logging
import os
import time
from contextlib import suppress

import httpx
from .contracts import Frame, KINDS, merge, split

logger = logging.getLogger('ingestion')


class Ingestion:
    def __init__(self, publish, current, *, mode=None, urls=None, token=None, transport=None):
        self.mode = mode or os.environ.get('INGESTION_MODE', 'embedded')
        if self.mode not in ('embedded', 'remote'):
            raise ValueError('INGESTION_MODE must be embedded or remote')
        self.urls = urls or {kind: os.environ.get(f'{kind.upper()}_INGESTION_URL', f'http://127.0.0.1:{8101+i}') for i, kind in enumerate(KINDS)}
        self.token = token if token is not None else os.environ.get('INGESTION_TOKEN', '')
        if self.mode == 'remote' and not self.token:
            raise ValueError('Remote ingestion requires INGESTION_TOKEN')
        self.publish = publish
        self.current = current
        self.queue = asyncio.Queue(maxsize=1)
        self.client = httpx.AsyncClient(timeout=.8, transport=transport, trust_env=False)
        self.task = None
        self.latest = None
        self.ready = asyncio.Event()
        self.status = {'mode': self.mode, 'status': 'starting', 'accepted': 0, 'failed': 0, 'coalesced': 0}

    async def normalize(self, snapshot):
        frames = split(snapshot)
        began = time.perf_counter()
        if self.mode == 'remote':
            async def send(frame):
                response = await self.client.post(self.urls[frame.kind].rstrip('/')+'/v1/normalize',
                    json=frame.model_dump(), headers={'Authorization': 'Bearer '+self.token})
                response.raise_for_status()
                normalized = Frame.model_validate(response.json())
                if normalized.kind != frame.kind or normalized.payload != frame.payload:
                    raise ValueError('Ingestion changed canonical simulator telemetry')
                return normalized
            frames = await asyncio.gather(*(send(frame) for frame in frames))
        result = merge(snapshot, frames)
        result['ingestion'] = {'mode': self.mode, 'services': list(KINDS), 'latency_ms': round((time.perf_counter()-began)*1000, 2)}
        return result

    def deliver(self, snapshot):
        if self.mode == 'embedded':
            # Same contracts, with no network/process requirement for Python tests.
            self.accept(merge(snapshot, split(snapshot)))
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.status['coalesced'] += 1
        self.queue.put_nowait(snapshot)

    def accept(self, snapshot):
        self.latest = snapshot
        self.status.update(status='ready', last_success_at=time.time())
        self.status['accepted'] += 1
        self.publish(snapshot)
        self.ready.set()

    async def current_snapshot(self):
        async with asyncio.timeout(2):
            while True:
                self.ready.clear()
                if self.latest and (self.latest['epoch'],self.latest['state_version'])==self.current() and time.time()-self.status.get('last_success_at',0)<5:
                    return copy.deepcopy(self.latest)
                await self.ready.wait()

    async def start(self, snapshot):
        self.accept(await self.normalize(snapshot))  # Fail startup if dependencies are unusable.
        if self.mode == 'remote':
            self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            snapshot = await self.queue.get()
            try:
                normalized = await self.normalize(snapshot)
                current = self.current()
                if (normalized['epoch'], normalized['state_version']) != current:
                    self.status['coalesced'] += 1
                    continue
                self.accept(normalized)
            except Exception:
                # No silent embedded fallback: consumers time out and disable controls.
                self.status['failed'] += 1
                self.status['status'] = 'degraded'
                logger.warning('Ingestion batch rejected or service unavailable')

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.client.aclose()
