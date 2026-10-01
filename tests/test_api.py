import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RAIL_DATA_DIR',str(tmp_path))
    from backend import app as module
    module=importlib.reload(module)
    with TestClient(module.app) as c:
        yield c,module


def login(c,admin=False):
    r=c.post('/api/login',json={'username':'admin' if admin else 'dispatcher','password':'demo-admin' if admin else 'demo-dispatch'})
    assert r.status_code==200


def test_auth_roles_openapi_and_health(client):
    c,m=client
    assert c.get('/health').status_code==200
    assert c.get('/api/state').status_code==401
    assert 'www-authenticate' not in c.get('/api/me').headers
    assert c.get('/api/state',auth=('dispatcher','demo-dispatch')).status_code==200
    login(c)
    settings=c.get('/api/config').json()
    assert c.put('/api/config',json=settings).status_code==403
    login(c,True)
    settings['weights']['importance']=7
    assert c.put('/api/config',json=settings).status_code==200
    assert m.engine.config.weights.importance==7
    assert '/api/incidents/batch' in c.get('/openapi.json').json()['paths']


def test_controls_dedup_ingestion_and_reports(client):
    c,m=client;login(c)
    c.post('/api/demo',json={'scenario':'passing'})
    data={'kind':'train_delay','asset_type':'train','asset_id':'KZ-101','duration_s':10}
    for _ in range(2):
        assert c.post('/api/incidents',json=data,headers={'Idempotency-Key':'same-command'}).status_code==201
    assert len(m.engine.incidents)==1
    assert c.post('/api/incidents',json={**data,'duration_s':20},headers={'Idempotency-Key':'same-command'}).status_code==409
    event={'source':'test','sequence':1,'sim_time':0,'incident':data}
    assert c.post('/api/ingest',json=event).status_code==200
    assert c.post('/api/ingest',json=event).status_code==409
    m.engine.advance(12)
    r=c.get('/api/replay?start_s=0&end_s=12').json()
    assert r['read_only'] and r['frames']
    assert c.get('/api/replay?start_s=0&end_s=901').status_code==422
    report=c.get('/api/report.csv')
    assert 'text/csv' in report.headers['content-type']
    assert 'incident' in report.text and 'quality' in report.text and 'train_summary' in report.text
    assert c.post('/api/control',json={'action':'pause'}).json()['running'] is False
    assert c.delete('/api/trains/KZ-101').status_code==409


def test_map_delivery_and_cross_origin_protection(client):
    c,m=client;login(c)
    original=(m.ROOT/'map.html').read_bytes()
    assert c.get('/map.html').content==original
    assert '/static/map-overlay.js' in c.get('/map-live').text
    assert c.post('/api/control',json={'action':'start'},headers={'Origin':'https://unrelated.example'}).status_code==403


def test_block_layout_change_requires_releasing_track(client):
    c,m=client; login(c,True)
    c.post('/api/demo',json={'scenario':'passing'})
    settings=c.get('/api/config').json()
    original=settings['signal_block_m']
    settings['signal_block_m']=1000
    result=c.put('/api/config',json=settings)
    assert result.status_code==409 and 'block layout' in result.json()['detail']
    assert m.engine.config.signal_block_m==original
    # A shorter lookahead affects future extensions, never an existing grant.
    grant=m.engine.trains['KZ-101'].authority
    settings['signal_block_m']=original
    settings['authority_lookahead_m']=1000
    assert c.put('/api/config',json=settings).status_code==200
    assert m.engine.trains['KZ-101'].authority>=grant
    c.post('/api/control',json={'action':'reset'})
    settings['signal_block_m']=1000
    assert c.put('/api/config',json=settings).status_code==200
    assert c.get('/api/state').json()['dispatcher']['signal_block_m']==1000
