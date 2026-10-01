"""Check selected candidate comparison, application and archive; resets the demo."""
import asyncio
import csv
import io
import json
from pathlib import Path
import sys

import httpx
import websockets


async def run(base,fixture_path=None):
    async with httpx.AsyncClient(base_url=base,timeout=20) as client:
        async def request(method,path,body=None,params=None):
            response=await client.request(method,path,json=body,params=params)
            response.raise_for_status()
            return response.json()

        async def get(path,params=None):return await request('GET',path,params=params)

        await request('POST','/api/simulation/reset')
        try:
            await request('PUT','/api/replanning',{'auto_apply':False,'policy':'balanced'})
            await request('POST','/api/incidents',{'kind':'delay','target_id':'T01','duration_s':600})
            async with asyncio.timeout(20):
                while True:
                    state=await get('/api/state')
                    assert state['replan_status']['status']!='failed',state['replan_status']
                    if state['replan_status']['status']=='review' and not state['replanning']:break
                    await asyncio.sleep(.1)
            plans=(await get('/api/plans'))['plans']
            recommended=state['replan_status']['recommended_plan_id']
            selected=next((p for p in plans if p['id']!=recommended),plans[0])
            data=await get(f'/api/plans/{selected["id"]}/comparison')
            change=data['comparison']
            assert data['applicable']
            assert change['new_plan_id']==selected['id'] and change['old_plan_id']==state['active_plan_id']
            assert change['before']['quality_signature']==change['after']['quality_signature']
            assert change['before']['conflicts']>0 and change['after']['conflicts']==0
            assert not change['before']['forecast_valid'] and change['after']['forecast_valid']
            assert (await get('/api/state'))['state_version']==state['state_version']
            url=base.replace('https://','wss://').replace('http://','ws://')+'/ws'
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()
                await request('POST',f'/api/plans/{selected["id"]}/apply')
                async with asyncio.timeout(5):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated' and event['payload']['active_plan_id']==selected['id']:
                            applied=event['payload'];break
            saved=applied['replan_status']['comparison']
            assert saved=={**change,'basis':'forecast_at_application'}
            assert saved['committed_total']==saved['committed_preserved']
            window=await get('/api/history/window')
            archive_url=f'/api/history/snapshots/{window["frames"][-1]["id"]}'
            archive_params={'epoch':applied['epoch']}
            archived=await get(archive_url,archive_params)
            assert archived['replan_status']['comparison']==saved
            params={**archive_params,'to':window['to_s'],'through_id':window['through_id']}
            report=await client.get('/api/reports/history.csv',params=params)
            report.raise_for_status()
            rows=list(csv.DictReader(io.StringIO(report.text.lstrip('\ufeff'))))
            exported=next(r for r in rows if r['record_type']=='replan' and r['metric']=='index')
            assert float(exported['after'])==saved['after']['index']
            assert float(exported['change'])==round(saved['after']['index']-saved['before']['index'],4)
            if fixture_path:
                destination=Path(fixture_path)
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_text(json.dumps({'snapshot':applied,'topology':await get('/api/topology')},ensure_ascii=False),encoding='utf-8')
            await request('POST','/api/simulation/reset')
            assert await get(archive_url,archive_params)==archived
            print(json.dumps({'status':'passed','candidates':len(plans),'nonrecommended_selection':selected['id']!=recommended,
                              'quality_before':saved['before']['index'],'quality_after':saved['after']['index'],
                              'conflicts_before':saved['before']['conflicts'],'conflicts_after':saved['after']['conflicts'],
                              'calculation_s':saved['calculation_s'],'websocket_comparison':True,'csv_matches':True,'archive_survives_reset':True}))
        finally:
            await request('POST','/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001',sys.argv[2] if len(sys.argv)>2 else None))
