"""Check stage 6 on a running local demo. Resets the scenario before/after."""
import asyncio
import json
import sys

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=20) as client:
        async def send(path,body=None,method='POST'):
            response=await client.request(method,path,json=body)
            response.raise_for_status();return response.json()
        async def get(path):
            return await send(path,method='GET')
        async def settled():
            async with asyncio.timeout(20):
                while True:
                    state=await get('/api/state')
                    if state['replan_status']['status']=='failed':raise AssertionError(state['replan_status'])
                    if state['replan_status']['status']=='applied' and not state['replanning']:return
                    await asyncio.sleep(.1)
        await send('/api/simulation/reset')
        try:
            initial=await get('/api/quality')
            assert initial['actual']['quality_version']==4
            assert initial['actual']['index']==100
            assert set(initial['actual']['components'])=={'schedule','energy','capacity','conflicts','arrival_accuracy'}
            assert initial['actual']['capacity']['utilization_pct'] is None
            url=base.replace('http://','ws://').replace('https://','wss://')+'/ws'
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()
                entry=await send('/api/incidents',{'kind':'closure','target_id':'section-1','duration_s':600})
                async with asyncio.timeout(10):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated' and event['payload']['incidents']:
                            metric=event['payload']['metrics']
                            assert metric['index']==96 and metric['components']['capacity']['loss_points']==4
                            break
            await settled()
            closed=await get('/api/quality')
            assert closed['actual']['index']==96
            assert closed['actual']['capacity']['availability_pct']==80
            assert {100,96}.issubset({p['index'] for p in closed['trend']['points']})
            await send('/api/incidents/'+entry['id']+'/resolve')
            await settled()
            assert (await get('/api/quality'))['actual']['index']==100
            await send('/api/simulation/speed',{'multiplier':30})
            await send('/api/simulation/start')
            async with asyncio.timeout(8):
                while (await get('/api/state'))['sim_time_s']<60:await asyncio.sleep(.05)
            await send('/api/simulation/pause')
            moving=await get('/api/quality')
            assert moving['actual']['capacity']['utilization_pct']>0
            assert moving['actual']['capacity']['distance_m']>0
            assert moving['actual']['components']['energy']['observed']
            settings=await get('/api/settings')
            settings['quality_weights'].update(schedule=30,energy=30)
            await send('/api/settings',settings,method='PUT')
            changed=await get('/api/quality')
            assert changed['actual']['quality_signature']!=initial['actual']['quality_signature']
            assert all(p['state_version']>=moving['state_version'] for p in changed['trend']['points'])
            print(json.dumps({'status':'passed','closure_score':closed['actual']['index'],
                              'closure_capacity_score':closed['actual']['components']['capacity']['score'],
                              'recovery_score':100,'utilization_pct':moving['actual']['capacity']['utilization_pct'],
                              'websocket_quality':True,'trend_records_closure':True,'formula_history_isolated':True}))
        finally:
            await send('/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
