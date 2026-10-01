import asyncio
import copy
import csv
import io
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.planning import shifted_seed
from backend.app.reports import csv_report, safe_cell
from backend.app.storage import Record, Store


def snapshot(epoch, version, now, score=100):
    return {'epoch':epoch,'state_version':version,'sim_time_s':now,'active_plan_id':'plan',
            'metrics':{'index':score,'conflicts':0,'quality_signature':'formula','formula':{'version':3}},
            'trains':[]}


def test_manifest_bounds_same_time_order_and_frozen_export_survive_reset_and_reopen(tmp_path):
    url=f'sqlite:///{tmp_path / "archive.sqlite"}'
    store=Store(url)
    for version,now in enumerate((0,299,300,600,1200,1200)):
        store.save('snapshot','first',now,snapshot('first',version,now,96 if version==5 else 100))
    window=store.archive_window('first',15)
    assert (window['from_s'],window['to_s'])==(300,1200)
    assert [frame['state_version'] for frame in window['frames']]==[2,3,4,5]
    assert all('trains' not in frame and 'metrics' not in frame for frame in window['frames'])
    assert len(store.archive_window('first',5)['frames'])==2
    assert len(store.archive_window('first',10)['frames'])==3
    exported=''.join(csv_report(store,window))
    store.save('snapshot','first',1200,snapshot('first',6,1200,90))
    store.save('snapshot','second',0,snapshot('second',0,0))
    frozen=store.archive_window('first',15,1200,window['through_id'])
    assert frozen==window
    assert ''.join(csv_report(store,frozen))==exported
    assert store.archive_snapshot('second',window['frames'][0]['id']) is None
    store.engine.dispose()
    reopened=Store(url)
    assert [r['epoch'] for r in reopened.runs()]==['second','first']
    assert reopened.archive_snapshot('first',window['frames'][-1]['id'])['metrics']['index']==96
    reopened.engine.dispose()


def test_archive_expired_missing_and_empty_windows(tmp_path):
    store=Store(f'sqlite:///{tmp_path / "expired.sqlite"}')
    store.save('snapshot','old',1000,snapshot('old',1,1000))
    assert store.archive_window('old',5,100)['frames']==[]
    assert store.archive_window('missing') is None
    record_id=store.archive_window('old')['frames'][0]['id']
    with Session(store.engine) as session:
        session.execute(update(Record).values(created_at=time.time()-store.retention_hours*3600-1))
        session.commit()
    assert store.runs()==[]
    assert store.archive_window('old') is None
    assert store.archive_snapshot('old',record_id) is None
    store.engine.dispose()


@pytest.mark.parametrize('text',["=SUM(A1:A2)",'+cmd','-cmd','@SUM(1)','  =1','\t42','\r42','\n42'])
def test_csv_formula_text_is_escaped(text):
    assert safe_cell(text)=="'"+text
    assert safe_cell(-600)==-600
    assert safe_cell('Астана, Кокшетау')=='Астана, Кокшетау'


async def quick_solver(state):
    return {'plans':[shifted_seed(state)]}


async def finish_job():
    await asyncio.wait_for(asyncio.shield(main.replanner.task),5)


def test_archive_report_incident_replan_playback_permissions_and_reset(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "api.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',quick_solver)
    with TestClient(main.app) as client:
        epoch=client.get('/api/state').json()['epoch']
        before=copy.deepcopy(main.sim.state)
        initial=client.get('/api/history/window',params={'minutes':15})
        assert initial.status_code==200,initial.text
        assert initial.json()['frames'][0]['quality_index']==100
        assert main.sim.state==before
        client.post('/api/incidents',json={'kind':'delay','target_id':'T01','duration_s':600})
        client.portal.call(finish_job)
        window=client.get('/api/history/window',params={'epoch':epoch,'minutes':5}).json()
        assert len({f['id'] for f in window['frames']})==len(window['frames'])>3
        assert len({f['sim_time_s'] for f in window['frames']})==1  # paused changes remain separate
        assert {'incident.created','plan.applied'}.issubset({e['type'] for e in window['events']})
        frame=window['frames'][1]
        before=copy.deepcopy(main.sim.state)
        replay=client.get(f'/api/history/snapshots/{frame["id"]}',params={'epoch':epoch})
        assert replay.status_code==200
        assert replay.json()['state_version']==frame['state_version']
        assert replay.json()['awaiting_plan']
        params={'epoch':epoch,'minutes':5,'to':window['to_s'],'through_id':window['through_id']}
        response=client.get('/api/reports/history.csv',params=params)
        assert response.status_code==200,response.text
        assert response.content.startswith(b'\xef\xbb\xbf')
        assert 'attachment;' in response.headers['content-disposition']
        rows=list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
        assert {'summary','quality','formula','component','train','conflict','event','replan','replan_train','replan_movement'}.issubset({r['record_type'] for r in rows})
        conflicts=next(r for r in rows if r['record_type']=='replan' and r['metric']=='conflicts')
        assert float(conflicts['before'])>0 and float(conflicts['after'])==0
        assert conflicts['basis']=='forecast_at_application'
        assert len([r for r in rows if r['record_type']=='replan_train'])==8
        assert all(r['epoch']==epoch for r in rows)
        assert main.sim.state==before  # replay/export never rewind the server
        client.post('/api/simulation/reset')
        new_epoch=main.sim.state['epoch']
        assert new_epoch!=epoch
        assert client.get('/api/reports/history.csv',params=params).content==response.content
        assert client.get('/api/history/runs').json()['current_epoch']==new_epoch
        assert len(client.get('/api/history/runs').json()['runs'])==2
        assert client.get(f'/api/history/snapshots/{frame["id"]}',params={'epoch':new_epoch}).status_code==404
        assert client.get('/api/history/window?minutes=60').status_code==422
        assert client.get('/api/history/window?to=nan').status_code==422
        assert client.get('/api/history/window?to=-1').status_code==422
        assert client.get('/api/history/window?epoch=missing').status_code==404
        monkeypatch.setattr(main,'demo_mode',False)
        paths=['/api/history/runs','/api/history/window',f'/api/history/snapshots/{frame["id"]}?epoch={epoch}']
        for path in paths:
            assert client.get(path).status_code==401
        assert client.get('/api/reports/history.csv',params=params).status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','viewer-test')
        client.post('/api/auth/login',json={'role':'viewer','password':'viewer-test'})
        for path in paths:
            assert client.get(path).status_code==200
        assert client.get('/api/reports/history.csv',params=params).status_code==200
        assert client.post('/api/simulation/start').status_code==403
