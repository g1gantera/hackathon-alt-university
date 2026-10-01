"""Final-stage demo against a running server; resets before and after."""
import asyncio
import csv
import io
import json
import sys

import httpx
import websockets


async def run(base):
    async with httpx.AsyncClient(base_url=base,timeout=25) as client:
        async def request(path,method='GET',body=None,params=None):
            response=await client.request(method,path,json=body,params=params)
            response.raise_for_status()
            return response

        await request('/api/simulation/reset','POST')
        try:
            initial=(await request('/api/state')).json()
            epoch=initial['epoch']
            await request('/api/incidents','POST',{'kind':'closure','target_id':'section-0','duration_s':600})
            async with asyncio.timeout(25):
                while True:
                    state=(await request('/api/state')).json()
                    assert state['replan_status']['status']!='failed',state['replan_status']
                    if state['replan_status']['status']=='applied' and not state['replanning']:
                        break
                    await asyncio.sleep(.1)
            await request('/api/simulation/speed','POST',{'multiplier':60})
            await request('/api/simulation/start','POST')
            async with asyncio.timeout(8):
                while (await request('/api/state')).json()['sim_time_s']<120:
                    await asyncio.sleep(.1)
            await request('/api/simulation/pause','POST')
            window=(await request('/api/history/window',params={'epoch':epoch,'minutes':5})).json()
            assert len(window['frames'])>=6
            assert min(frame['quality_index'] for frame in window['frames'])<100
            held_frame=None
            for frame in window['frames']:
                saved=(await request(f'/api/history/snapshots/{frame["id"]}',params={'epoch':epoch})).json()
                assert saved['epoch']==epoch and saved['state_version']==frame['state_version']
                if saved['awaiting_plan']:
                    held_frame=frame
            assert held_frame
            stable=(await request('/api/state')).json()
            params={'epoch':epoch,'minutes':5,'to':window['to_s'],'through_id':window['through_id']}
            async with websockets.connect(base.replace('http','ws',1)+'/ws',origin=base) as ws:
                await ws.recv()
                report=await request('/api/reports/history.csv',params=params)
                async with asyncio.timeout(5):
                    while json.loads(await ws.recv())['type']!='state.updated':
                        pass
            rows=list(csv.DictReader(io.StringIO(report.text.lstrip('\ufeff'))))
            comparison=next(row for row in rows if row['record_type']=='replan' and row['metric']=='conflicts')
            assert float(comparison['before'])>0 and float(comparison['after'])==0
            assert any(row['record_type']=='conflict' for row in rows)
            assert any(row['record_type']=='train' and row['metric']=='delay_s' for row in rows)
            assert (await request('/api/state')).json()['state_version']==stable['state_version']
            await request('/api/simulation/reset','POST')
            assert (await request('/api/reports/history.csv',params=params)).content==report.content
            runs=(await request('/api/history/runs')).json()
            assert runs['current_epoch']!=epoch and any(item['epoch']==epoch for item in runs['runs'])
            html=await request('/')
            assert 'assets/' in html.text
            print(json.dumps({'status':'passed','snapshots':len(window['frames']),'events':len(window['events']),
                              'csv_rows':len(rows),'replanned_conflicts_before':float(comparison['before']),
                              'replanned_conflicts_after':float(comparison['after']),
                              'replay_read_only':True,'report_survives_reset':True,'websocket_during_export':True}))
        finally:
            await request('/api/simulation/reset','POST')


if __name__=='__main__':
    asyncio.run(run(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'))
