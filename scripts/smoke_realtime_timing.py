"""Measure a local demo's feed and incident load; resets before and after."""
import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets


async def run(base):
    url=base.replace('https://','wss://').replace('http://','ws://')+'/ws'
    async with httpx.AsyncClient(base_url=base,timeout=20) as client:
        async def post(path,body=None):
            response=await client.post(path,json=body)
            response.raise_for_status()
            return response.json()

        async def state():
            response=await client.get('/api/state')
            response.raise_for_status()
            return response.json()

        await post('/api/simulation/reset')
        try:
            await post('/api/simulation/speed',{'multiplier':7})
            await post('/api/simulation/start')
            async with websockets.connect(url,origin=base) as ws:
                initial=json.loads(await ws.recv())
                assert initial['update_interval_ms']==500
                frames=[]
                async with asyncio.timeout(12):
                    while len(frames)<13:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated':
                            frames.append((time.perf_counter(),event['payload']))
            gaps=[right[0]-left[0] for left,right in zip(frames,frames[1:])]
            assert statistics.mean(gaps)<.6 and max(gaps)<1,gaps
            # Twelve half-second intervals at 7x must advance exactly 42 model seconds.
            elapsed=frames[-1][1]['sim_time_s']-frames[0][1]['sim_time_s']
            assert elapsed==42,elapsed
            assert all(frame[1]['realtime']['update_interval_ms']==500 for frame in frames)

            arrivals=[]
            applied=asyncio.Event()
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()

                async def receive():
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']!='state.updated':
                            continue
                        arrivals.append(time.perf_counter())
                        snapshot=event['payload']
                        assert snapshot['replan_status']['status']!='failed',snapshot['replan_status']
                        if len(snapshot['incidents'])==10 and snapshot['replan_status']['status']=='applied' and not snapshot['replanning']:
                            assert snapshot['dispatch']['valid']
                            applied.set()

                receiver=asyncio.create_task(receive())
                try:
                    incidents=[{'kind':'closure','target_id':f'section-{i}','duration_s':600+i*60} for i in range(5)]
                    incidents += [{'kind':'signal','target_id':f'section-{i}','duration_s':900} for i in range(3)]
                    incidents += [{'kind':'delay','target_id':f'T0{i}','duration_s':900} for i in (3,4)]
                    began=time.perf_counter()
                    await asyncio.gather(*(post('/api/incidents',incident) for incident in incidents))
                    waiter=asyncio.create_task(applied.wait())
                    try:
                        done,_=await asyncio.wait((receiver,waiter),timeout=15,return_when=asyncio.FIRST_COMPLETED)
                        if receiver in done:
                            await receiver  # Surface a receiver assertion or disconnected socket.
                        assert waiter in done,'Replanning did not settle within 15 seconds'
                    finally:
                        waiter.cancel()
                        await asyncio.gather(waiter,return_exceptions=True)
                    replan_seconds=time.perf_counter()-began
                    assert replan_seconds<=5,f'End-to-end replanning exceeded 5 seconds: {replan_seconds:.3f}s'
                    await asyncio.sleep(2)
                finally:
                    receiver.cancel()
                    result=await asyncio.gather(receiver,return_exceptions=True)
                    if isinstance(result[0],Exception):
                        raise result[0]
            load_gaps=[b-a for a,b in zip(arrivals,arrivals[1:])]
            assert load_gaps and max(load_gaps)<1,load_gaps
            await post('/api/simulation/pause')
            paused=await state()
            await asyncio.sleep(1.1)
            assert (await state())['sim_time_s']==paused['sim_time_s']
            print(json.dumps({'status':'passed','target_hz':2,
                              'mean_interval_ms':round(statistics.mean(gaps)*1000,2),
                              'max_interval_ms':round(max(gaps)*1000,2),
                              'model_seconds_in_6_real_seconds':elapsed,'speed_multiplier':7,
                              'incident_count':10,'replanning_s':round(replan_seconds,3),
                              'max_interval_under_load_ms':round(max(load_gaps)*1000,2),
                              'conflicts_after':paused['metrics']['conflicts'],'pause_verified':True}))
        finally:
            await post('/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1].rstrip('/') if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
