import copy
from fastapi.testclient import TestClient
from backend.app import main


def test_api_history_csv_roles_and_stale_plan(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "test.sqlite"}')
    with TestClient(main.app) as client:
        assert client.get('/api/topology').status_code==200
        before=client.get('/api/state').json()
        assert len(before['trains'])==8
        assert client.post('/api/simulation/speed',json={'multiplier':15}).status_code==200
        assert client.get('/api/trains/T01/profile').json()['reachable']
        assert client.get('/api/report.csv').text.count('\n')==9
        assert client.get('/api/history').status_code==200
        assert client.get('/api/state').json()['sim_time_s']==before['sim_time_s']
        assert client.post('/api/incidents',json={'kind':'closure','target_id':'invalid','duration_s':600}).status_code==404
        # Revalidation rejects a once-valid plan after simulated time has moved on.
        plan=copy.deepcopy(main.sim.state['active_plan'])
        plan.update(epoch=main.sim.state['epoch'],constraint_version=0)
        main.sim.plans[plan['id']]=plan
        main.sim.state['sim_time_s']=100000
        assert client.post(f'/api/plans/{plan["id"]}/apply').status_code==409
        monkeypatch.setattr(main,'demo_mode',False)
        monkeypatch.setenv('VIEWER_PASSWORD','viewer-test')
        assert client.post('/api/simulation/start').status_code==401
        assert client.post('/api/auth/login',json={'role':'viewer','password':'viewer-test'}).status_code==200
        assert client.get('/api/state').status_code==200
        assert client.post('/api/simulation/start').status_code==403
        assert client.put('/api/settings',json=main.DEFAULT_SETTINGS).status_code==403
        assert client.post('/api/auth/logout').status_code==200
        assert client.get('/api/state').status_code==401
