import copy
import math

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.dispatch import dispatch_report, priority_weight
from backend.app.planning import build_plans, solve
from backend.app.simulator import Simulator
from backend.app.validation import validate_plan


@pytest.fixture
def state():
    return Simulator().state


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),True,None,'0',1.5])
def test_invalid_times_are_rejected_without_crashing(state,value):
    plan=copy.deepcopy(state['active_plan'])
    plan['movements'][0]['start_s']=value
    assert validate_plan(state,plan)[0]['code']=='time'


def test_unknown_missing_duplicate_movements_rejected(state):
    for mutate in (lambda moves:moves.pop(),lambda moves:moves.append(moves[0]),
                   lambda moves:moves[0].update(train_id='unknown'),lambda moves:moves[0].pop('section_id')):
        plan=copy.deepcopy(state['active_plan'])
        mutate(plan['movements'])
        assert validate_plan(state,plan)[0]['code']=='route'


@pytest.mark.parametrize('other_id,kind',[('T02','opposing'),('T03','following')])
def test_conflict_detection_reports_direction_and_overlap(state,other_id,kind):
    plan=copy.deepcopy(state['active_plan'])
    first=next(m for m in plan['movements'] if m['train_id']=='T01' and m['leg']==0)
    other=next(m for m in plan['movements'] if m['train_id']==other_id and m['section_id']==first['section_id'])
    shift=first['start_s']-other['start_s']
    for key in ('start_s','end_s','release_s'):
        other[key]+=shift
    report=dispatch_report(state,plan)
    conflicts=[c for c in report['conflicts'] if c['kind']==kind and {c['train_id'],c['other_train_id']}=={'T01',other_id}]
    assert not report['valid'] and conflicts
    assert conflicts[0]['start_s']<conflicts[0]['end_s']


def test_exact_release_boundary_and_one_second_overlap(state):
    plan=copy.deepcopy(state['active_plan'])
    pair=sorted((m for m in plan['movements'] if m['section_id']=='section-0'),key=lambda m:m['start_s'])[:2]
    pair[0]['release_s']=pair[1]['start_s']
    def pair_conflicts():
        return [e for e in validate_plan(state,plan) if e['code']=='occupancy' and e['resource']=='section-0'
                and {e['train_id'],e['other_train_id']}=={m['train_id'] for m in pair}]
    assert not pair_conflicts()
    pair[0]['release_s']+=1
    assert pair_conflicts()


def test_report_has_complete_resource_order_and_honest_waits(state):
    report=dispatch_report(state,state['active_plan'])
    assert report['valid'] and not report['conflicts']
    assert len(report['next_decisions'])==8
    assert len(report['decisions'])==40
    for group in report['section_order']:
        for first,second in zip(group['reservations'],group['reservations'][1:]):
            assert first['release_s']<=second['start_s']
    waits=[d for d in report['decisions'] if d['predecessors']]
    assert waits
    for decision in waits:
        assert decision['wait_s']==decision['start_s']-decision['ready_s']
        assert all(decision['ready_s']<p['release_s']<=decision['start_s'] for p in decision['predecessors'])
    state['awaiting_plan']=True
    assert all(d['action']=='held' for d in dispatch_report(state,state['active_plan'])['next_decisions'])


@pytest.mark.parametrize('preferred',['A','B'])
def test_priority_changes_order_for_equal_requests(state,preferred):
    template=copy.deepcopy(state['fleet'][0])
    state['fleet']=[{**template,'id':name,'priority':10 if name==preferred else 1} for name in ('A','B')]
    state.pop('active_plan');state.pop('baseline')
    plan=solve(state,False,2)
    assert plan and validate_plan(state,plan)==[]
    departures=sorted((m for m in plan['movements'] if m['leg']==0),key=lambda m:m['start_s'])
    assert departures[0]['train_id']==preferred
    assert plan['policy']=='balanced'


def test_passenger_policy_increases_only_passenger_penalty(state):
    for train in state['fleet']:
        multiplier=3 if train['type']=='passenger' else 1
        assert priority_weight(state,train,True)==multiplier*priority_weight(state,train,False)


def test_no_valid_schedule_does_not_replace_active_plan(state):
    original=copy.deepcopy(state['active_plan'])
    state['topology']['stations'][0]['tracks']=1
    result=build_plans(state)
    assert result['plans']==[]
    assert result['status']=='no_valid_plan'
    assert state['active_plan']==original


def test_solver_preserves_a_started_nondefault_duration_and_clearance(state):
    # One train isolates the committed movement from unrelated capacity/order constraints.
    state['fleet']=state['fleet'][:1]
    for field in ('active_plan','baseline'):
        state[field]=copy.deepcopy(state[field])
        state[field]['movements']=[m for m in state[field]['movements'] if m['train_id']=='T01']
    first=state['active_plan']['movements'][0]
    first['end_s']+=60;first['release_s']+=90
    for m in state['active_plan']['movements'][1:]:
        for key in ('start_s','end_s','release_s'):
            m[key]+=90
    state['committed']=[['T01',0]];state['sim_time_s']=30
    assert validate_plan(state,state['active_plan'])==[]
    plan=solve(state,False,1)
    assert plan and validate_plan(state,plan)==[]
    assert next(m for m in plan['movements'] if m['leg']==0)==first


def test_dispatch_api_validation_roles_and_apply(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "dispatch.sqlite"}')
    with TestClient(main.app) as client:
        report=client.get('/api/dispatch').json()
        assert report['valid'] and report['active'] and not report['applicable']
        assert len(report['decisions'])==40
        assert client.get('/api/dispatch?plan_id=missing').status_code==404
        state=main.sim.state
        candidate=copy.deepcopy(state['active_plan'])
        candidate.update(id='candidate',epoch=state['epoch'],constraint_version=state['constraint_version'])
        main.sim.plans[candidate['id']]=candidate
        assert client.get('/api/dispatch?plan_id=candidate').json()['applicable']
        assert client.post('/api/plans/candidate/apply').status_code==200
        assert main.sim.state['active_plan']['id']=='candidate'
        # Apply checks the actual data, not a stored "violations: []" marker.
        invalid=copy.deepcopy(candidate);invalid['id']='invalid'
        invalid['movements'][0]['release_s']=invalid['movements'][0]['end_s']
        main.sim.plans['invalid']=invalid
        assert client.post('/api/plans/invalid/apply').status_code==409
        assert main.sim.state['active_plan']['id']=='candidate'
        main.sim.state['active_plan']=invalid
        assert client.post('/api/simulation/start').status_code==409
        assert not main.sim.state['running']
        main.sim.state['active_plan']=candidate
        # All completed journeys remain inspectable and do not gain phantom waits.
        state['running']=True;main.sim.tick(math.ceil(max(m['release_s'] for m in candidate['movements'])))
        assert all(d['action']=='completed' for d in client.get('/api/dispatch').json()['next_decisions'])
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get('/api/dispatch').status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','view')
        client.post('/api/auth/login',json={'role':'viewer','password':'view'})
        assert client.get('/api/dispatch').status_code==200
        assert client.post('/api/replan').status_code==403
        assert client.post('/api/plans/candidate/apply').status_code==403
