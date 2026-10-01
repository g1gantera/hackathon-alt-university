import copy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.domain import DWELL, movement_profile, started
from backend.app.replanning import Replanner
from backend.app.simulator import Simulator
from backend.app.speed_advice import eco_plan, train_profile
from backend.app.validation import validate_plan


def train(sim, ident='T01'):
    return next(t for t in sim.snapshot()['trains'] if t['id']==ident)


def test_live_advice_acceleration_cruise_braking_and_completion():
    sim=Simulator()
    original=copy.deepcopy(sim.state)
    assert train(sim)['speed_advice']['phase']=='waiting'
    assert train(sim)['speed_advice']['recommended_speed_mps']==0
    assert sim.state==original
    sim.state['running']=True
    first=next(m for m in sim.state['active_plan']['movements'] if m['train_id']=='T01' and m['leg']==0)
    for when,phase in [(1,'accelerating'),(300,'cruising'),(first['end_s']-10,'braking')]:
        sim.tick(when-sim.state['sim_time_s'])
        item=train(sim)
        advice=item['speed_advice']
        assert advice['phase']==phase
        assert advice['recommended_speed_mps']==pytest.approx(item['speed_mps'])
        assert 0<=advice['lookahead_speed_mps']<=advice['limit_mps']<=25
        assert advice['next_arrival_s']==first['end_s']
        assert 0<advice['energy_remaining_kwh']<advice['energy_plan_kwh']
    sim.tick(100000)
    for item in sim.snapshot()['trains']:
        advice=item['speed_advice']
        assert advice['phase']=='completed'
        assert advice['recommended_speed_mps']==advice['lookahead_speed_mps']==0
        assert advice['energy_remaining_kwh']==0


def test_incident_holds_waiting_advice_but_preserves_committed_profile():
    sim=Simulator();sim.state['running']=True;sim.tick(300)
    before=train(sim)['speed_advice']
    sim.state['awaiting_plan']=True
    sim.state['incidents']=[{'id':'test','kind':'closure','target_id':'section-0','start_s':300,'end_s':1800}]
    moving=train(sim)['speed_advice']
    held=train(sim,'T03')['speed_advice']
    assert moving['recommended_speed_mps']==before['recommended_speed_mps']
    assert moving['next_arrival_s']==before['next_arrival_s']
    assert moving['reason']=='clear_committed_section' and moving['final_arrival_s'] is None
    assert held['recommended_speed_mps']==0 and held['phase']=='held'
    assert held['departure_s'] is None and held['next_arrival_s'] is None
    assert held['energy_remaining_kwh'] is None
    full=train_profile(sim.state,sim.state['fleet'][0])
    assert full['provisional'] and full['energy_kwh'] is None
    assert max(p[0] for p in full['points'])==pytest.approx(before['next_arrival_s'])
    assert train_profile(sim.state,sim.state['fleet'][2])['points']==[]


def test_reverse_direction_and_station_waits_in_detailed_profile():
    sim=Simulator();item=train(sim,'T02')
    assert item['speed_advice']['next_station_id']=='burabay'
    profile=train_profile(sim.state,item)
    assert all(a[0]<=b[0] and a[1]<=b[1] for a,b in zip(profile['points'],profile['points'][1:]))
    assert profile['points'][-1][1]==pytest.approx(sim.state['topology']['length_m'])
    gaps=[(a,b) for a,b in zip(profile['points'],profile['points'][1:]) if a[1]==b[1] and b[0]-a[0]>=DWELL]
    assert gaps and all(a[2]==b[2]==0 for a,b in gaps)
    sim.state['running']=True;sim.tick(300)
    assert train(sim,'T02')['speed_advice']['recommended_speed_mps']>0


@pytest.mark.parametrize('ident',['T01','T02','T03','T04','T05','T06','T07','T08'])
def test_eco_proposal_preserves_constraints_departures_and_final_arrivals(ident):
    sim=Simulator();original=copy.deepcopy(sim.state)
    result=eco_plan(sim.state,ident)
    assert sim.state==original
    if not result['plan']:
        assert result['reason']=='no_safe_slack'
        return
    candidate=result['plan']
    assert validate_plan(sim.state,candidate)==[]
    old={(m['train_id'],m['leg']):m for m in sim.state['active_plan']['movements']}
    for m in candidate['movements']:
        previous=old[(m['train_id'],m['leg'])]
        assert m['start_s']==previous['start_s']
        if m['train_id']!=ident or m['leg']==4:
            assert m==previous
        fleet_train=next(t for t in sim.state['fleet'] if t['id']==m['train_id'])
        section=next(s for s in sim.state['topology']['sections'] if s['id']==m['section_id'])
        profile=movement_profile(fleet_train,section,m['end_s']-m['start_s'])
        points=np.asarray(profile['points'])
        assert profile['reachable'] and max(points[:,2])<=min(section['speed_limit_mps'],fleet_train['max_speed_mps'])
        assert points[0,2]==points[-1,2]==0
        acceleration=np.diff(points[:,2])/np.diff(points[:,0])
        assert acceleration.max()<=(.45 if fleet_train['type']=='passenger' else .2)+1e-6
        assert acceleration.min()>=-(.65 if fleet_train['type']=='passenger' else .4)-1e-6
    assert candidate['eco']['saving_kwh']>0
    assert candidate['metrics']['energy_kwh']<sim.state['active_plan']['metrics']['energy_kwh']
    assert result['profile']['energy_kwh']==pytest.approx(candidate['eco']['after_energy_kwh'],abs=.01)


def test_eco_application_changes_actual_movement_and_consumption():
    sim=Simulator();sim.state['running']=True;sim.tick(7100);sim.state['running']=False
    committed=[copy.deepcopy(m) for m in sim.state['active_plan']['movements'] if started(sim.state,m)]
    baseline_end=max(m['end_s'] for m in sim.state['active_plan']['movements'] if m['train_id']=='T05')
    original=copy.deepcopy(sim.state['active_plan'])
    proposal=eco_plan(sim.state,'T05')['plan']
    assert proposal
    coordinator=Replanner(sim,None,lambda *args:None,lambda:None,lambda p:None)
    coordinator.install(proposal)
    assert all(m in sim.state['active_plan']['movements'] for m in committed)
    sim.state['running']=True;sim.tick(11000-sim.state['sim_time_s'])
    item=train(sim,'T05');advice=item['speed_advice']
    assert item['speed_mps']<20  # baseline cruise was almost 25 m/s
    assert advice['recommended_speed_mps']==pytest.approx(item['speed_mps'])
    assert advice['final_arrival_s']==baseline_end
    sim.tick(100000)
    actual=train(sim,'T05')['energy_kwh']
    fast=sum(movement_profile(sim.state['fleet'][4],sim.state['topology']['sections'][m['leg']],m['end_s']-m['start_s'])['energy_kwh'] for m in original['movements'] if m['train_id']=='T05')
    assert fast-actual==pytest.approx(proposal['eco']['saving_kwh'],abs=.01)


def test_eco_plan_refuses_invalid_incident_state_and_obsolete_source():
    sim=Simulator()
    proposal=eco_plan(sim.state,'T05')['plan']
    coordinator=Replanner(sim,None,lambda *args:None,lambda:None,lambda p:None)
    sim.state['active_plan']['id']='different-plan'
    with pytest.raises(ValueError,match='Source timetable'):
        coordinator.install(proposal)
    sim.state['awaiting_plan']=True
    assert eco_plan(sim.state,'T05')['reason']=='awaiting_valid_plan'


def test_speed_advice_api_preview_apply_history_and_roles(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "speed_advice.sqlite"}')
    with TestClient(main.app) as client:
        data=client.get('/api/trains/T05/advice').json()
        assert data['advice']['recommended_speed_mps']==0
        assert data['profile']['train_id']=='T05' and data['profile']['reachable']
        assert client.get('/api/trains/missing/advice').status_code==404
        assert client.post('/api/trains/missing/eco-plan').status_code==404
        initial=client.get('/api/state').json()['active_plan_id']
        response=client.post('/api/trains/T05/eco-plan')
        assert response.status_code==200
        result=response.json();plan=result['plan']
        assert result['base_plan_id']==initial and plan['eco']['saving_kwh']>50
        assert client.get('/api/state').json()['active_plan_id']==initial
        assert client.post('/api/plans/'+plan['id']+'/apply').status_code==200
        state=client.get('/api/state').json()
        assert state['active_plan_id']==plan['id']
        assert next(t for t in state['trains'] if t['id']=='T05')['speed_advice']['energy_saving_kwh']>50
        assert any(next(t for t in s['trains'] if t['id']=='T05')['speed_advice']['energy_saving_kwh']>50 for s in client.get('/api/history').json())
        client.post('/api/simulation/start')
        assert client.post('/api/trains/T05/eco-plan').status_code==409
        client.post('/api/simulation/reset')
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get('/api/trains/T05/advice').status_code==401
        assert client.post('/api/trains/T05/eco-plan').status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','viewer')
        client.post('/api/auth/login',json={'role':'viewer','password':'viewer'})
        assert client.get('/api/trains/T05/advice').status_code==200
        assert client.post('/api/trains/T05/eco-plan').status_code==403


@pytest.mark.parametrize('change',['reset','incident','new_plan','start'])
def test_in_flight_eco_result_is_discarded_after_scenario_change(tmp_path,monkeypatch,change):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "stale_eco.sqlite"}')
    async def solve(snapshot,ident):
        result=eco_plan(snapshot,ident)
        if change=='reset':
            main.sim.reset()
        elif change=='incident':
            main.sim.state['constraint_version']+=1
            main.sim.state['awaiting_plan']=True
        elif change=='new_plan':
            main.sim.state['active_plan']['id']='replacement'
        else:
            main.sim.state['running']=True
        return result
    monkeypatch.setattr(main,'solve_eco',solve)
    with TestClient(main.app) as client:
        assert client.post('/api/trains/T05/eco-plan').status_code==409
        assert main.sim.plans=={}
