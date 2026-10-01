import copy
import json
import time
import numpy as np
import pytest

from backend.app.advisory import profile, sample
from backend.app.domain import topology, trains, ROOT
from backend.app.metrics import DEFAULT_SETTINGS
from backend.app.planning import build_plans, heuristic
from backend.app.simulator import Simulator
from backend.app.validation import validate_plan


@pytest.fixture(scope='module')
def base():
    return {'topology':topology(),'fleet':trains(),'sim_time_s':0,'state_version':0,'speed':30,
            'running':False,'incidents':[],'settings':dict(DEFAULT_SETTINGS),'committed':[]}


@pytest.fixture(scope='module')
def solved(base):
    result=build_plans(copy.deepcopy(base))
    assert result['plans'], result
    assert result['within_budget'], result['elapsed_s']
    return result['plans'][0]


def test_corridor_is_connected_and_station_ordered(base):
    topo=base['topology']
    assert 290000<topo['length_m']<330000
    assert len(topo['stations'])==6
    for a,b in zip(topo['sections'],topo['sections'][1:]):
        assert a['geometry'][-1]==b['geometry'][0]
    assert [s['position_m'] for s in topo['stations']]==sorted(s['position_m'] for s in topo['stations'])


def test_profiles_obey_physics_and_windows():
    p=profile(12000,25,650000,True)
    points=np.asarray(p['points'])
    assert p['reachable']
    assert points[0,2]==0 and points[-1,2]==0
    assert max(points[:,2])<=25
    accel=np.diff(points[:,2])/np.diff(points[:,0])
    assert max(accel)<=.450001 and min(accel)>=-.650001
    assert abs(points[-1,1]-12000)<.001
    assert abs(points[-1,0]-p['duration_s'])<.001
    assert not profile(12000,25,650000,True,p['minimum_time_s']-1)['reachable']
    slower=profile(12000,25,650000,True,p['duration_s']+120)
    assert slower['energy_kwh']<p['energy_kwh']
    assert sample(p,p['duration_s'])[0]==pytest.approx(12000)


def test_solver_and_fallback_are_independently_validated(base,solved):
    assert len(solved['movements'])==40
    assert validate_plan(base,solved)==[]
    fallback=heuristic(base)
    assert fallback and not validate_plan(base,fallback)


@pytest.mark.parametrize('kind',['closure','signal','delay'])
def test_incidents_change_feasible_departures(base,solved,kind):
    state=copy.deepcopy(base)
    state['active_plan']=copy.deepcopy(solved)
    state['baseline']=copy.deepcopy(solved)
    first=min(solved['movements'],key=lambda x:x['start_s'])
    target=first['train_id'] if kind=='delay' else first['section_id']
    state['incidents']=[{'id':'incident','kind':kind,'target_id':target,'leg':first['leg'],'start_s':0,'created_s':0,'end_s':7200}]
    assert validate_plan(state,solved)
    result=build_plans(state)
    assert result['plans']
    for plan in result['plans']:
        assert not validate_plan(state,plan)
        move=next(m for m in plan['movements'] if m['train_id']==first['train_id'] and m['leg']==first['leg'])
        assert move['start_s']>=7200


def test_rejects_collision_and_wrong_physics(base,solved):
    plan=copy.deepcopy(solved)
    section=plan['movements'][0]['section_id']
    pair=[m for m in plan['movements'] if m['section_id']==section][:2]
    shift=pair[0]['start_s']-pair[1]['start_s']
    for field in ('start_s','end_s','release_s'):
        pair[1][field]+=shift
    assert 'occupancy' in {v['code'] for v in validate_plan(base,plan)}
    plan=copy.deepcopy(solved)
    plan['movements'][0]['end_s']=plan['movements'][0]['start_s']+1
    assert 'physics' in {v['code'] for v in validate_plan(base,plan)}


def test_rejects_station_capacity_dwell_switch_and_past(base,solved):
    state=copy.deepcopy(base)
    state['topology']['stations'][0]['tracks']=1
    assert 'capacity' in {v['code'] for v in validate_plan(state,solved)}
    plan=copy.deepcopy(solved)
    one=[m for m in plan['movements'] if m['train_id']=='T01']
    one[1]['start_s']=one[0]['end_s']
    assert 'dwell' in {v['code'] for v in validate_plan(base,plan)}
    plan=copy.deepcopy(solved)
    terminal=[m for m in plan['movements'] if m['leg']==0 and m['train_id'] in ('T01','T03')]
    terminal[1]['start_s']=terminal[0]['start_s']
    assert 'switch' in {v['code'] for v in validate_plan(base,plan)}
    state=copy.deepcopy(base)
    state['active_plan']=copy.deepcopy(solved)
    state['sim_time_s']=100
    first=min(solved['movements'],key=lambda x:x['start_s'])
    state['committed']=[[first['train_id'],first['leg']]]
    plan=copy.deepcopy(solved)
    changed=next(m for m in plan['movements'] if m['train_id']==first['train_id'] and m['leg']==first['leg'])
    changed['start_s']+=10
    assert 'past' in {v['code'] for v in validate_plan(state,plan)}


def test_simulation_is_deterministic_and_holds_unstarted_trains():
    s=Simulator()
    initial=copy.deepcopy(s.state)
    s.reset()
    assert s.state['active_plan']==initial['active_plan']
    s.state['running']=True
    s.tick(600)
    first=s.snapshot()
    s.state=copy.deepcopy(initial)
    s.state['running']=True
    s.tick(600)
    repeated=s.snapshot()
    first.pop('epoch')
    repeated.pop('epoch')
    assert first==repeated
    s.state=copy.deepcopy(initial)
    s.state.update(running=True,awaiting_plan=True)
    s.tick(3600)
    assert all(t['speed_mps']==0 and t['status']=='waiting' for t in s.snapshot()['trains'])


def test_replan_retains_a_reasonable_incumbent():
    s=Simulator()
    s.state['running']=True
    s.tick(1200)
    s.state['awaiting_plan']=True
    s.state['incidents']=[{'id':'close','kind':'closure','target_id':'section-1','start_s':1200,'created_s':1200,'end_s':4800}]
    result=build_plans(copy.deepcopy(s.state))
    assert result['plans']
    assert result['within_budget']
    assert all(not validate_plan(s.state,p) for p in result['plans'])
    assert min(p['metrics']['max_delay_s'] for p in result['plans'])<7200


def test_ten_incidents_use_one_bounded_cycle(base,solved):
    state=copy.deepcopy(base)
    state['baseline']=solved
    state['active_plan']=solved
    state['incidents']=[{'id':str(i),'kind':'closure' if i%2==0 else 'signal','target_id':f'section-{i%5}',
                        'start_s':0,'created_s':0,'end_s':600+i*60} for i in range(10)]
    result=build_plans(state)
    assert result['within_budget']
    assert result['plans']
    assert all(not validate_plan(state,p) for p in result['plans'])
