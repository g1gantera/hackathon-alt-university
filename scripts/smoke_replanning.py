"""Check stage 4 against a running local demo; resets the scenario before/after."""
import asyncio
import json
import sys

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=15) as client:
        async def send(path,body=None,method='POST'):
            response=await client.request(method,path,json=body)
            response.raise_for_status()
            return response.json()
        async def state():
            response=await client.get('/api/state');response.raise_for_status()
            return response.json()
        async def settle(expected):
            async with asyncio.timeout(15):
                while True:
                    snapshot=await state()
                    if snapshot['replan_status']['status']=='failed':
                        raise AssertionError(snapshot['replan_status'])
                    if snapshot['replan_status']['status']==expected and not snapshot['replanning']:
                        return snapshot
                    await asyncio.sleep(.1)
        await send('/api/simulation/reset')
        try:
            await send('/api/simulation/speed',{'multiplier':30})
            await send('/api/simulation/start')
            async with asyncio.timeout(5):
                while (await state())['sim_time_s']==0:
                    await asyncio.sleep(.05)
            before=await state()
            committed=[m for m in before['plan']['movements'] if m['start_s']<before['sim_time_s']]
            assert committed
            url=base.replace('https://','wss://').replace('http://','ws://')+'/ws'
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()
                entries=[]
                for kind,target,duration in [('closure','section-1',3600),('signal','section-4',1800),('delay','T03',2400)]:
                    entries.append(await send('/api/incidents',{'kind':kind,'target_id':target,'duration_s':duration}))
                updates=0
                async with asyncio.timeout(15):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated':
                            updates+=1
                            snapshot=event['payload']
                            if snapshot['replan_status']['status']=='failed':raise AssertionError(snapshot['replan_status'])
                            if snapshot['replan_status']['status']=='applied' and len(snapshot['incidents'])==3 and not snapshot['replanning']:
                                break
            automatic=await state()
            assert automatic['dispatch']['valid'] and not automatic['awaiting_plan']
            assert all(m in automatic['plan']['movements'] for m in committed)
            assert automatic['replan_status']['automatic']
            closure,signal,delay=entries
            for m in automatic['plan']['movements']:
                if m in committed:continue
                if m['section_id']==closure['target_id']:assert m['start_s']>=closure['end_s']
                if m['section_id']==signal['target_id']:assert m['start_s']>=signal['end_s']
                if m['train_id']=='T03' and m['leg']==0:assert m['start_s']>=delay['end_s']
            await send('/api/simulation/pause')
            await send(f'/api/incidents/{closure["id"]}/resolve')
            resolved=await settle('applied')
            assert resolved['replan_status']['trigger']=='resolved'
            assert next(i for i in resolved['incidents'] if i['id']==closure['id'])['status']=='resolved'
            await send('/api/replanning',{'auto_apply':False,'policy':'balanced'},'PUT')
            waiting=next(t for t in resolved['trains'] if t['status']=='waiting')
            await send('/api/incidents',{'kind':'delay','target_id':waiting['id'],'duration_s':600})
            reviewed=await settle('review')
            assert reviewed['awaiting_plan']
            await send(f'/api/plans/{reviewed["replan_status"]["recommended_plan_id"]}/apply')
            applied=await state()
            assert not applied['awaiting_plan'] and not applied['replan_status']['automatic']
            print(json.dumps({'status':'passed','incident_types':3,'state_updates_during_replan':updates,
                              'committed_preserved':len(committed),'automatic_apply':True,'early_resolution':True,
                              'manual_review':True,'conflicts_after':applied['dispatch']['conflicts']}))
        finally:
            await send('/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1].rstrip('/') if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
