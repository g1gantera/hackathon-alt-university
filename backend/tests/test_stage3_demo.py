import copy

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.stage3_demo import demo_state, solve_demo
from backend.app.validation import validate_plan


@pytest.mark.parametrize('scenario',['opposing','following','fleet'])
def test_demo_resolves_real_conflicts_with_complete_routes(scenario):
    result=solve_demo(scenario,{},'balanced')
    assert result['before']['conflicts']
    assert result['after']['valid'] and result['after']['conflicts']==[]
    assert len(result['after']['decisions'])==len(result['trains'])*5
    plan={'movements':[{k:m[k] for k in ('train_id','leg','section_id','start_s','end_s','release_s')}
                       for m in result['after']['decisions']]}
    assert validate_plan(demo_state(scenario),plan)==[]
    for group in result['after']['section_order']:
        assert all(a['release_s']<=b['start_s'] for a,b in zip(group['reservations'],group['reservations'][1:]))
    for m in plan['movements']:
        points=result['profiles'][f'{m["train_id"]}:{m["leg"]}']
        section=next(s for s in result['sections'] if s['id']==m['section_id'])
        assert points[0]==[0,0]
        assert points[-1][0]==m['end_s']-m['start_s']
        assert abs(points[-1][1]-section['length_m'])<.02
        assert all(a[0]<b[0] and a[1]<=b[1] for a,b in zip(points,points[1:]))


def test_sandbox_state_and_requested_priorities_are_independent():
    first=demo_state('opposing',{'D01':1,'D02':10})
    second=demo_state('opposing')
    assert first['fleet'][1]['priority']==10
    assert second['fleet'][1]['priority']==1
    first['settings']['passenger_weight']=19
    assert second['settings']['passenger_weight']==3


def test_demo_api_never_mutates_main_simulation(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "demo.sqlite"}')
    with TestClient(main.app) as client:
        before=copy.deepcopy(main.sim.state)
        plans=copy.deepcopy(main.sim.plans)
        assert client.get('/api/stage3/scenario?scenario=following').status_code==200
        assert client.get('/api/stage3/scenario?scenario=bad').status_code==422
        assert client.post('/api/stage3/solve',json={'priorities':{'unknown':1}}).status_code==422
        assert client.post('/api/stage3/solve',json={'priorities':{'D01':11}}).status_code==422
        response=client.post('/api/stage3/solve',json={'scenario':'following','policy':'passenger_priority','priorities':{'D01':1,'D02':10}})
        assert response.status_code==200
        assert response.json()['after']['valid']
        assert main.sim.state==before and main.sim.plans==plans
        assert not main.sim.replanning
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get('/api/stage3/scenario').status_code==401
        assert client.post('/api/stage3/solve',json={}).status_code==401
