"""Exercise the local stage-3 flow. Leaves the demo paused after a short run.

Usage: python scripts/smoke_dispatch.py [http://127.0.0.1:8001]
"""
import asyncio
import json
import sys
import time

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=15) as client:
        async def post(path,body=None):
            result=await client.post(path,json=body)
            result.raise_for_status()
            return result.json()
        await post('/api/simulation/pause')
        before=(await client.get('/api/state')).json()
        url=base.replace('https://','wss://').replace('http://','ws://')+'/ws'
        async with websockets.connect(url,origin=base) as ws:
            await ws.recv()
            began=time.perf_counter()
            await post('/api/replan')
            updates=0
            async with asyncio.timeout(15):
                while True:
                    event=json.loads(await ws.recv())
                    if event['type']=='state.updated':
                        updates+=1
                    if event['type']=='replan.failed':
                        raise AssertionError(event['payload'])
                    if event['type']=='replan.completed':
                        plans=event['payload']['plans']
                        break
        elapsed=time.perf_counter()-began
        for plan in plans:
            response=await client.get('/api/dispatch',params={'plan_id':plan['id']})
            response.raise_for_status()
            report=response.json()
            assert report['valid'] and report['applicable'] and not report['conflicts']
            assert len(report['decisions'])==40
            for group in report['section_order']:
                for first,second in zip(group['reservations'],group['reservations'][1:]):
                    assert first['release_s']<=second['start_s']
        await post(f'/api/plans/{plans[0]["id"]}/apply')
        active=(await client.get('/api/dispatch')).json()
        assert active['active'] and active['valid'] and active['plan_id']==plans[0]['id']
        await post('/api/simulation/speed',{'multiplier':30})
        try:
            await post('/api/simulation/start')
            async with websockets.connect(url,origin=base) as ws:
                async with asyncio.timeout(10):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated' and event['payload']['sim_time_s']>before['sim_time_s']:
                            assert event['payload']['dispatch']['valid']
                            break
        finally:
            await post('/api/simulation/pause')
            await post('/api/simulation/speed',{'multiplier':before['speed']})
        print(json.dumps({'status':'passed','plan_count':len(plans),'movements_per_plan':40,
                          'conflicts':0,'calculation_wall_s':round(elapsed,3),'state_updates_during_calculation':updates}))


if __name__=='__main__':
    asyncio.run(run(sys.argv[1].rstrip('/') if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
