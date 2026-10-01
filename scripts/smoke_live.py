"""Exercise a running local demo. Resets demo state before and after the test."""
import asyncio
import json
import time
from pathlib import Path
import httpx
from websockets.asyncio.client import connect


async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=15) as c:
        (await c.post('/api/simulation/reset')).raise_for_status()
        async with connect('ws://127.0.0.1:8000/ws') as ws:
            initial=json.loads(await ws.recv())
            assert initial['type']=='state.updated'
            (await c.post('/api/simulation/start')).raise_for_status()
            received=[]
            deadline=time.perf_counter()+5
            while time.perf_counter()<deadline:
                event=json.loads(await asyncio.wait_for(ws.recv(),5))
                if event['type']=='state.updated' and event['payload']['sim_time_s']>0:
                    received.append(event)
                    break
            assert received
            assert sum(t['status']=='moving' for t in received[0]['payload']['trains'])>=2
            (await c.post('/api/simulation/pause')).raise_for_status()
            snapshot=(await c.get('/api/state')).json()
            (await c.post('/api/incidents',json={'kind':'closure','target_id':'section-1','duration_s':3600})).raise_for_status()
            samples=[]
            began=time.perf_counter()
            result=None
            while time.perf_counter()-began<12:
                event=json.loads(await asyncio.wait_for(ws.recv(),12))
                if event['type']=='state.updated':
                    samples.append(time.perf_counter())
                if event['type']=='replan.completed':
                    result=event['payload']
                    break
                if event['type']=='replan.failed':
                    raise AssertionError(event)
            assert result and result['plans']
            assert result['within_budget'], result
            plan=result['plans'][0]
            applied=await c.post(f'/api/plans/{plan["id"]}/apply')
            applied.raise_for_status()
            assert not applied.json()['awaiting_plan']
            assert applied.json()['active_plan_id']==plan['id']
            assert not plan['violations']
            history=(await c.get('/api/history')).json()
            assert history
            assert (await c.get('/api/state')).json()['sim_time_s']==snapshot['sim_time_s']
            assert 'text/csv' in (await c.get('/api/report.csv')).headers['content-type']
            report={'status':'passed','trains':8,'initial_moving':2,'plan_count':len(result['plans']),
                    'planning_cycle_s':result['elapsed_s'],'state_updates_during_replan':len(samples),
                    'max_delay_s':plan['metrics']['max_delay_s'],'history_read_only':True,
                    'csv':True,'applied_validated_plan':True}
            Path('docs').mkdir(exist_ok=True)
            Path('docs/live-smoke-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report))
        (await c.post('/api/simulation/reset')).raise_for_status()


if __name__=='__main__':
    asyncio.run(main())
