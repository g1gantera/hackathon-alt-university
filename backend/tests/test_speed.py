import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from backend.app import main, simulator
from backend.app.planning import build_plans, ready_time
from backend.app.replanning import Replanner
from backend.app.simulator import Simulator
from backend.app.timing import MAX_SPEED
from backend.app.validation import validate_plan


def test_large_jump_matches_individual_seconds():
    fast=Simulator();slow=Simulator();slow.state=copy.deepcopy(fast.state)
    fast.state['running']=slow.state['running']=True
    for jump in (1,59,540,900,3500,20000,100000):
        fast.tick(jump)
        for _ in range(jump):slow.tick(1)
        assert fast.state==slow.state
    assert not fast.state['running']
    assert fast.state['sim_time_s']==max(m['release_s'] for m in fast.state['active_plan']['movements'])


def test_maximum_browser_speed_has_bounded_work(monkeypatch):
    sim=Simulator();sim.state.update(running=True,speed=MAX_SPEED)
    original=simulator.started
    calls=0
    def counted(state,move):
        nonlocal calls
        calls+=1
        assert calls<500, 'Clock work must depend on scheduled events, not elapsed seconds'
        return original(state,move)
    monkeypatch.setattr(simulator,'started',counted)
    sim.tick(MAX_SPEED)
    assert len(sim.state['committed'])==40 and not sim.state['running']
    assert calls<500
    assert all(t['status']=='completed' for t in sim.snapshot()['trains'])


def test_high_speed_waits_for_a_valid_replacement_then_resumes():
    async def run():
        sim=Simulator();sim.state.update(running=True,speed=1000000,awaiting_plan=True)
        sim.state['incidents']=[{'id':'closure','kind':'closure','target_id':'section-0','leg':None,'created_s':0,'start_s':0,'end_s':600}]
        async def solve(snapshot):
            sim.tick(sim.state['speed'])
            assert sim.state['sim_time_s']==snapshot['sim_time_s']
            assert sim.snapshot()['clock_held_for_replan']
            return build_plans(snapshot)
        coordinator=Replanner(sim,solve,lambda *a:None,lambda:None,lambda p:None,debounce=0)
        coordinator.request('incident',True)
        await coordinator.task
        assert sim.state['replan_status']['status']=='applied'
        assert not sim.clock_held_for_replan
        assert validate_plan(sim.state,sim.state['active_plan'])==[]
        sim.tick(sim.state['speed'])
        assert not sim.state['running']
    asyncio.run(run())


def test_lowering_speed_allows_time_to_advance_during_incident_hold():
    sim=Simulator();sim.state.update(running=True,speed=1000,awaiting_plan=True)
    sim.tick(1000)
    assert sim.state['sim_time_s']==0
    sim.state['speed']=30
    sim.tick(30)
    assert sim.state['sim_time_s']==30 and sim.state['committed']==[]
    assert ready_time(sim.state,sim.state['fleet'][0],0)==210
    sim.state['speed']=MAX_SPEED
    assert ready_time(sim.state,sim.state['fleet'][0],0)==30


def test_custom_speed_api_validation(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "speed.sqlite"}')
    with TestClient(main.app) as client:
        for speed in (1,7,100,1000,1000000,MAX_SPEED):
            response=client.post('/api/simulation/speed',json={'multiplier':speed})
            assert response.status_code==200 and response.json()['speed']==speed
        for speed in (0,-1,1.5,True,'100',None,MAX_SPEED+1):
            assert client.post('/api/simulation/speed',json={'multiplier':speed}).status_code==422
        assert client.get('/api/state').json()['speed']==MAX_SPEED
