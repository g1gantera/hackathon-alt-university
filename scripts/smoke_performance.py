"""Local demo acceptance: 3 incident types and 5/10 incident bursts, repeated.

Uses a disposable server: resets before each case and finally. Measures from the
first HTTP request until a WebSocket snapshot carries the validated applied plan,
including debounce, solver retries, storage and transport. Not browser latency.
"""
import argparse
import asyncio
import json
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets


def scenarios():
    closure = {'kind': 'closure', 'target_id': 'section-1', 'duration_s': 900}
    signal = {'kind': 'signal', 'target_id': 'section-2', 'duration_s': 900}
    delay = {'kind': 'delay', 'target_id': 'T03', 'duration_s': 900}
    five = [dict(closure, target_id=f'section-{i}') for i in range(5)]
    ten = five + [dict(signal, target_id=f'section-{i}') for i in range(3)]
    ten += [dict(delay, target_id=f'T0{i}') for i in (3, 4)]
    return [('closure', [closure]), ('signal', [signal]), ('delay', [delay]),
            ('five_incidents', five), ('ten_incidents', ten)]


async def run(base, repetitions):
    results = []
    url = base.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws'
    async with httpx.AsyncClient(base_url=base, timeout=20) as client:
        async def post(path, body=None):
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        try:
            for repetition in range(repetitions):
                for name, incidents in scenarios():
                    await post('/api/simulation/reset')
                    await post('/api/simulation/speed', {'multiplier': 7})
                    await post('/api/simulation/start')
                    async with websockets.connect(url, origin=base) as ws:
                        initial = json.loads(await ws.recv())['payload']
                        epoch = initial['epoch']
                        # Allow ordinary moving frames as well as incident-load frames.
                        await asyncio.sleep(2)
                        frames = []
                        began = time.perf_counter()

                        async def receive():
                            applied = None
                            async with asyncio.timeout(15):
                                while True:
                                    event = json.loads(await ws.recv())
                                    if event['type'] != 'state.updated':
                                        continue
                                    snapshot = event['payload']
                                    now = time.perf_counter()
                                    assert snapshot['epoch'] == epoch
                                    frames.append(now)
                                    assert snapshot['replan_status']['status'] != 'failed', snapshot['replan_status']
                                    if applied is None and len(snapshot['incidents']) == len(incidents) and snapshot['replan_status']['status'] == 'applied' and not snapshot['replanning']:
                                        assert snapshot['dispatch']['valid']
                                        assert not snapshot['awaiting_plan']
                                        assert snapshot['metrics']['conflicts'] == 0
                                        applied = now
                                    if applied is not None and now-applied >= 2:
                                        return applied-began

                        receiver = asyncio.create_task(receive())
                        try:
                            await asyncio.gather(*(post('/api/incidents', body) for body in incidents))
                            elapsed = await receiver
                        finally:
                            receiver.cancel()
                            await asyncio.gather(receiver, return_exceptions=True)
                    # Discard queued pre-request frames by using intervals after the burst.
                    gaps = [b-a for a, b in zip(frames, frames[1:]) if a-began > .1]
                    result = {'case': name, 'repetition': repetition+1, 'incidents': len(incidents),
                              'epoch': epoch, 'request_to_applied_s': round(elapsed, 4),
                              'max_state_interval_ms': round(max(gaps)*1000, 2), 'conflicts_after': 0}
                    results.append(result)
                    print(json.dumps(result), flush=True)
                    assert elapsed <= 5, result
                    assert gaps and max(gaps) < 1, result
        finally:
            await post('/api/simulation/reset')
    timings = [row['request_to_applied_s'] for row in results]
    return {'status': 'passed', 'measured_at': datetime.now(timezone.utc).isoformat(),
            'platform': platform.platform(), 'python': platform.python_version(),
            'limits': {'request_to_applied_s': 5, 'state_interval_ms': 1000},
            'case_count': len(results), 'median_replanning_s': statistics.median(timings),
            'max_replanning_s': max(timings), 'cases': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', nargs='?', default='http://127.0.0.1:8012')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions must be positive')
    result = asyncio.run(run(args.base.rstrip('/'), args.repetitions))
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded+'\n', encoding='utf-8')
    print(encoded)
