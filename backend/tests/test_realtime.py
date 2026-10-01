from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import main


def next_state(ws, predicate=lambda snapshot: True):
    for _ in range(30):
        event=ws.receive_json()
        if event['type']=='state.updated' and predicate(event['payload']):
            assert event['seq']==event['payload']['realtime']['seq']
            assert event['epoch']==event['payload']['epoch']
            return event
    raise AssertionError('No matching state received')


def test_live_contract_pause_movement_reset_reconnect(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "live.sqlite"}')
    with TestClient(main.app) as client:
        feed=client.get('/api/trains').json()
        assert feed['realtime']['source']=='simulation'
        assert feed['realtime']['update_interval_ms']==500
        assert datetime.fromisoformat(feed['realtime']['observed_at']).tzinfo
        assert len(feed['trains'])==8
        train=client.get('/api/trains/T01').json()['train']
        assert len(train['coordinate'])==2
        assert len(train['route_station_ids'])==6
        assert train['speed_mps']==train['delay_s']==0
        assert client.get('/api/trains/unknown').status_code==404
        with client.websocket_connect('/ws') as ws:
            initial=next_state(ws)
            heartbeat=next_state(ws)
            assert heartbeat['seq']>initial['seq']
            assert heartbeat['payload']['sim_time_s']==initial['payload']['sim_time_s']
            assert heartbeat['observed_at']>initial['observed_at']
            client.post('/api/simulation/speed',json={'multiplier':60})
            client.post('/api/simulation/start')
            moved=next_state(ws,lambda s:s['sim_time_s']>=60)
            moving=[t for t in moved['payload']['trains'] if t['status']=='moving']
            assert moving and any(t['speed_mps']>0 for t in moving)
            assert any(t['coordinate']!=next(x for x in feed['trains'] if x['id']==t['id'])['coordinate'] for t in moving)
            client.post('/api/simulation/pause')
            paused=next_state(ws,lambda s:not s['running'])
            again=next_state(ws)
            assert again['payload']['sim_time_s']==paused['payload']['sim_time_s']
            client.post('/api/simulation/reset')
            reset=next_state(ws,lambda s:s['epoch']!=initial['epoch'])
            assert reset['payload']['sim_time_s']==0
            assert reset['stream_id']==initial['stream_id']
        with client.websocket_connect('/ws') as ws:
            reconnected=next_state(ws)
            assert reconnected['epoch']==reset['epoch']
            assert reconnected['seq']>=reset['seq']
            assert len(reconnected['payload']['trains'])==8
    with TestClient(main.app) as client:
        with client.websocket_connect('/ws') as ws:
            assert next_state(ws)['stream_id']!=initial['stream_id']


def test_feed_requires_auth_and_websocket_expires(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "auth.sqlite"}')
    monkeypatch.setattr(main,'demo_mode',False)
    monkeypatch.setenv('VIEWER_PASSWORD','test-viewer')
    with TestClient(main.app) as client:
        assert client.get('/api/trains').status_code==401
        assert client.get('/api/trains/T01').status_code==401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws'):
                pass
        client.post('/api/auth/login',json={'role':'viewer','password':'test-viewer'})
        with client.websocket_connect('/ws') as ws:
            next_state(ws)
            client.post('/api/auth/logout')
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(10):
                    ws.receive_json()
            assert closed.value.code==1008
