import asyncio
import copy

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.planning import shifted_seed


async def quick_solver(snapshot):
    plan=shifted_seed(snapshot)
    assert plan
    return {'plans':[plan]}


async def finish_job():
    await asyncio.wait_for(asyncio.shield(main.replanner.task),5)


def test_closure_quality_update_reaches_websocket_history_and_recovery(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "quality.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',quick_solver)
    with TestClient(main.app) as client:
        assert client.get('/api/state').json()['metrics']['index']==100
        with client.websocket_connect('/ws') as ws:
            assert ws.receive_json()['payload']['metrics']['index']==100
            entry=client.post('/api/incidents',json={'kind':'closure','target_id':'section-1','duration_s':600}).json()
            # The first incident snapshot is published before the solver result.
            for _ in range(10):
                event=ws.receive_json()
                if event['type']=='state.updated' and event['payload']['incidents']:
                    metric=event['payload']['metrics']
                    assert metric['index']==96
                    assert metric['blocked_sections']==['section-1']
                    assert metric['conflicts']==len(event['payload']['dispatch']['conflicts'])
                    break
            else:
                raise AssertionError('Missing incident metric update on WebSocket')
        client.portal.call(finish_job)
        assert client.get('/api/state').json()['metrics']['index']==96
        assert any(s['metrics']['index']==96 for s in client.get('/api/history').json())
        client.post(f'/api/incidents/{entry["id"]}/resolve')
        client.portal.call(finish_job)
        assert client.get('/api/state').json()['metrics']['index']==100
        client.post('/api/simulation/reset')
        assert client.get('/api/state').json()['metrics']['index']==100


def test_auto_manual_resolve_reset_and_permissions(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "replan.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',quick_solver)
    with TestClient(main.app) as client:
        assert client.get('/api/replanning').json()['options']['auto_apply']
        baseline=main.sim.state['active_plan']['id']
        event=client.post('/api/incidents',json={'kind':'delay','target_id':'T01','duration_s':600}).json()
        client.portal.call(finish_job)
        state=client.get('/api/state').json()
        assert state['replan_status']['status']=='applied'
        assert state['active_plan_id']!=baseline and not state['awaiting_plan']
        assert state['replan_status']['comparison']['after']['conflicts']==0
        assert any(s['replan_status']['status']=='applied' for s in client.get('/api/history').json())
        assert client.post(f'/api/incidents/{event["id"]}/resolve').status_code==200
        client.portal.call(finish_job)
        resolved=client.get('/api/state').json()
        assert resolved['incidents'][0]['status']=='resolved'
        assert resolved['replan_status']['trigger']=='resolved'
        version=main.sim.state['constraint_version']
        assert client.post(f'/api/incidents/{event["id"]}/resolve').status_code==200
        assert main.sim.state['constraint_version']==version
        assert client.post('/api/incidents/missing/resolve').status_code==404
        assert client.put('/api/replanning',json={'auto_apply':False,'policy':'passenger_priority'}).status_code==200
        client.post('/api/incidents',json={'kind':'signal','target_id':'section-1','duration_s':600})
        client.portal.call(finish_job)
        assert main.sim.state['replan_status']['status']=='review' and main.sim.state['awaiting_plan']
        plan_id=client.get('/api/plans').json()['plans'][0]['id']
        assert client.post(f'/api/plans/{plan_id}/apply').status_code==200
        assert not main.sim.state['awaiting_plan']
        assert not main.sim.state['replan_status']['automatic']
        old_epoch=main.sim.state['epoch']
        client.post('/api/simulation/reset')
        assert main.sim.state['epoch']!=old_epoch and main.sim.state['replan_status']['status']=='idle'
        assert not main.sim.state['incidents']
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get('/api/replanning').status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','viewer')
        client.post('/api/auth/login',json={'role':'viewer','password':'viewer'})
        assert client.get('/api/replanning').status_code==200
        assert client.put('/api/replanning',json={'auto_apply':True}).status_code==403
        assert client.post('/api/replanning/retry').status_code==403
        assert client.post('/api/incidents/missing/resolve').status_code==403
        assert client.post('/api/incidents',json={'kind':'closure','target_id':'section-0'}).status_code==403


def test_incident_expiry_retries_failed_plan_once(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "expiry.sqlite"}')
    calls=[]
    async def solve(snapshot):
        calls.append(copy.deepcopy(snapshot))
        return {'plans':[]} if len(calls)==1 else await quick_solver(snapshot)
    monkeypatch.setattr(main,'solve_snapshot',solve)
    with TestClient(main.app) as client:
        client.post('/api/incidents',json={'kind':'closure','target_id':'section-0','duration_s':30})
        client.portal.call(finish_job)
        assert main.sim.state['replan_status']['status']=='failed'
        client.post('/api/simulation/speed',json={'multiplier':60})
        client.post('/api/simulation/start')
        async def wait_for_recovery():
            async with asyncio.timeout(5):
                while main.sim.state['replan_status']['status']!='applied':
                    await asyncio.sleep(.02)
        client.portal.call(wait_for_recovery)
        client.post('/api/simulation/pause')
        assert len(calls)==2 and main.sim.state['incidents'][0]['expiry_notified']
        assert client.get('/api/state').json()['incidents'][0]['status']=='expired'
        assert not main.sim.state['awaiting_plan']
        assert main.sim.state['replan_status']['trigger']=='expiry'
