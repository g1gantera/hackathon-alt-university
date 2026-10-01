import copy
import time

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.integration import LogicSimulator, build_plans, validate_plan


def test_logic_live_state_profiles_and_decision_hold():
    sim = LogicSimulator()
    assert len(sim.state['topology']['stations']) == 13
    assert all(len(s['main_tracks']) == 2 for s in sim.state['topology']['sections'])
    sim.state['running'] = True
    sim.tick(500)
    before = sim.snapshot()
    assert any(t['status'] == 'moving' for t in before['trains'])
    sim.add_incident('closure', sim.state['topology']['sections'][0]['id'], 600)
    sim.tick(60)
    assert sim.state['sim_time_s'] == 500
    result = build_plans(sim.state)
    assert result['plans'], result['diagnostics']
    for plan in result['plans']:
        assert not validate_plan(sim.state, plan)
        old = {(m['train_id'], m['leg']):m for m in sim.state['active_plan']['movements'] if m['start_s']<=500}
        assert all(m == old[m['train_id'],m['leg']] for m in plan['movements'] if (m['train_id'],m['leg']) in old)
    sim.state['active_plan'] = result['plans'][0]
    sim.state['awaiting_plan'] = False
    sim.tick(60)
    assert sim.state['sim_time_s'] == 560
    for train in sim.snapshot()['trains']:
        assert 0 <= train['position_m'] <= sim.state['topology']['length_m'] + 1
        assert sim.profile(train['id'])['reachable']


def test_logic_api_plan_apply_history_csv_and_scenario(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPATCH_ENGINE','logic')
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "logic.sqlite"}')
    with TestClient(main.app) as client:
        initial = client.get('/api/state').json()
        assert initial['engine'] == 'logic'
        assert '_native' not in initial['plan']
        assert len(client.get('/api/topology').json()['stations']) == 13
        assert client.get('/api/trains/'+initial['trains'][0]['id']+'/profile').json()['reachable']
        assert client.get('/api/report.csv').text.count('\n') == 9
        assert client.post('/api/replan').status_code == 200
        deadline = time.monotonic()+25
        while time.monotonic()<deadline and (main.job_task is None or not main.job_task.done()):
            time.sleep(.1)
        plans = client.get('/api/plans').json()['plans']
        assert plans
        assert client.post('/api/plans/'+plans[0]['id']+'/apply').status_code == 200
        assert client.put('/api/settings',json=main.DEFAULT_SETTINGS).status_code == 200
        assert client.get('/api/logic/diagnostics').json()['plan']['movements']
        # History reads do not advance or mutate the simulation.
        before = copy.deepcopy(main.sim.state)
        assert client.get('/api/history').status_code == 200
        assert main.sim.state == before
        assert 'railsim_snow' in client.get('/api/logic/scenarios').json()['scenarios']
        assert client.post('/api/logic/scenarios/unknown').status_code == 404
        # Direct scenario loading preserves the native restrictions for replanning.
        assert client.post('/api/logic/scenarios/railsim_snow').status_code == 200
        assert main.sim.state['awaiting_plan']
        assert any(s['entry_speed_limits'] for s in main.sim.state['scenario']['sections'])
