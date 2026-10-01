"""Verify runtime quality configuration on a demo server; resets before/after."""
import asyncio
import csv
import io
import json
import sys

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=20) as client:
        async def request(method,path,body=None,params=None):
            result=await client.request(method,path,json=body,params=params)
            result.raise_for_status()
            return result.json()

        async def get(path,params=None):
            return await request('GET',path,params=params)

        async def save(settings):
            await request('PUT','/api/settings',settings)
            return (await get('/api/state'))['metrics']

        await request('POST','/api/simulation/reset')
        try:
            defaults=await get('/api/settings')
            initial=await get('/api/state')
            assert initial['metrics']['index']==100 and initial['metrics']['quality_version']==4
            await request('POST','/api/incidents',{'kind':'closure','target_id':'section-1','duration_s':600})
            async with asyncio.timeout(20):
                while True:
                    closed=await get('/api/state')
                    assert closed['replan_status']['status']!='failed',closed['replan_status']
                    if closed['replan_status']['status']=='applied' and not closed['replanning']:break
                    await asyncio.sleep(.1)
            assert closed['metrics']['index']==96
            window=await get('/api/history/window')
            archive_url='/api/history/snapshots/'+str(window['frames'][-1]['id'])
            archive_params={'epoch':closed['epoch']}
            archived=await get(archive_url,archive_params)
            settings={**defaults,'quality_weights':{key:100 if key=='capacity' else 0 for key in defaults['quality_weights']},
                      'quality_threshold_normal':85,'quality_threshold_attention':75}
            url=base.replace('https://','wss://').replace('http://','ws://')+'/ws'
            async with websockets.connect(url,origin=base) as ws:
                await ws.recv()
                score=await save(settings)
                async with asyncio.timeout(5):
                    while True:
                        event=json.loads(await ws.recv())
                        if event['type']=='state.updated' and event['payload']['metrics']['quality_signature']==score['quality_signature']:
                            assert event['payload']['metrics']['index']==80
                            assert event['payload']['metrics']['assessment']=='attention'
                            break
            assert score['index']==80 and score['assessment']=='attention'
            assert (await get(archive_url,archive_params))==archived
            assert {(p['index']) for p in (await get('/api/quality'))['trend']['points']}=={80}
            settings.update(quality_threshold_attention=85,quality_threshold_normal=95)
            critical=await save(settings)
            assert critical['index']==80 and critical['assessment']=='disrupted'
            settings.update(quality_threshold_normal=90,quality_threshold_attention=70)
            settings['quality_weights'].update(schedule=50,capacity=50)
            arithmetic=await save(settings)
            assert arithmetic['index']==90 and arithmetic['assessment']=='on_track'
            settings['quality_formula']='weighted_geometric'
            geometric=await save(settings)
            assert geometric['index']==89.4 and geometric['assessment']=='attention'
            invalid=await client.put('/api/settings',json={**settings,'quality_weights':{key:0 for key in settings['quality_weights']}})
            assert invalid.status_code==422
            assert (await get('/api/state'))['metrics']==geometric
            window=await get('/api/history/window')
            report=await client.get('/api/reports/history.csv',params={'epoch':closed['epoch'],'to':window['to_s'],'through_id':window['through_id']})
            report.raise_for_status()
            rows=list(csv.DictReader(io.StringIO(report.text.lstrip('\ufeff'))))
            assert {'on_track','attention','disrupted'}.issubset({r['value'] for r in rows if r['metric']=='assessment'})
            assert any(json.loads(r['details']).get('aggregation')=='weighted_geometric' for r in rows if r['record_type']=='formula')
            await request('POST','/api/simulation/reset')
            assert await get('/api/settings')==defaults
            assert await get(archive_url,archive_params)==archived
            print(json.dumps({'status':'passed','default_closure':96,'capacity_only':80,'weighted_mean':90,'weighted_geometric':89.4,
                              'websocket_settings':True,'threshold_categories':True,'archive_unchanged':True,'csv_formula_and_category':True,'reset_defaults':True}))
        finally:
            await request('POST','/api/simulation/reset')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
