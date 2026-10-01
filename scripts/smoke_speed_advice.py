"""Exercise stage 5 against a running local demo; resets it before and after."""
import asyncio
import json
import sys

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=20) as client:
        async def get(path):
            response=await client.get(path);response.raise_for_status();return response.json()
        async def post(path,body=None):
            response=await client.post(path,json=body);response.raise_for_status();return response.json()
        def selected(state,ident='T05'):
            return next(t for t in state['trains'] if t['id']==ident)
        await post('/api/simulation/reset')
        try:
            initial=await get('/api/trains/T05/advice')
            assert initial['advice']['recommended_speed_mps']==0
            result=await post('/api/trains/T05/eco-plan')
            plan=result['plan'];assert plan and plan['eco']['saving_kwh']>50
            await post('/api/plans/'+plan['id']+'/apply')
            await post('/api/simulation/speed',{'multiplier':1000})
            url=base.replace('http://','ws://').replace('https://','wss://')+'/ws'
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()
                await post('/api/simulation/start')
                async with asyncio.timeout(18):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']!='state.updated':continue
                        state=event['payload']
                        if state['sim_time_s']>=11000:break
            await post('/api/simulation/pause')
            moving=selected(await get('/api/state'))
            assert moving['status']=='moving' and moving['section_id']=='section-1'
            assert 0<moving['speed_mps']<20
            assert moving['speed_advice']['recommended_speed_mps']==moving['speed_mps']
            assert moving['speed_advice']['final_arrival_s']==initial['advice']['final_arrival_s']
            await post('/api/incidents',{'kind':'closure','target_id':'section-1','duration_s':600})
            held=await get('/api/state')
            assert selected(held)['speed_advice']['reason']=='clear_committed_section'
            assert selected(held,'T03')['speed_advice']['recommended_speed_mps']==0
            assert selected(held,'T03')['speed_advice']['phase']=='held'
            async with asyncio.timeout(18):
                while True:
                    state=await get('/api/state')
                    if state['replan_status']['status']=='failed':raise AssertionError(state['replan_status'])
                    if state['replan_status']['status']=='applied' and not state['replanning']:break
                    await asyncio.sleep(.1)
            assert state['dispatch']['valid']
            assert selected(state)['speed_advice']['recommended_speed_mps']==moving['speed_mps']
            print(json.dumps({'status':'passed','forecast_saving_kwh':plan['eco']['saving_kwh'],
                              'speed_kmh':round(moving['speed_mps']*3.6,2),'terminal_eta_preserved':True,
                              'websocket_advice':True,'incident_hold':True,'committed_profile_preserved':True}))
        finally:
            await post('/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
