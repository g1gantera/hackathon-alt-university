import asyncio
import copy

import pytest

from backend.app.domain import started
from backend.app.planning import build_plans, shifted_seed
from backend.app.replanning import Replanner
from backend.app.simulator import Simulator
from backend.app.validation import validate_plan


def add_incident(sim,kind='closure',target='section-0',duration=600):
    now=sim.state['sim_time_s']
    incident={'id':str(len(sim.state['incidents'])),'kind':kind,'target_id':target,
              'start_s':now,'created_s':now,'end_s':now+duration,'leg':0 if kind=='delay' else None}
    sim.state['incidents'].append(incident)
    sim.state['constraint_version']+=1
    sim.state['awaiting_plan']=True
    return incident


def setup(sim,solve):
    events=[]
    saved=[]
    coordinator=Replanner(sim,solve,lambda kind,payload:events.append((kind,copy.deepcopy(payload))),
                          lambda:None,lambda plan:saved.append(copy.deepcopy(plan)),debounce=0)
    return coordinator,events,saved


async def seed_solver(snapshot):
    plan=shifted_seed(snapshot)
    assert plan is not None
    return {'plans':[plan]}


@pytest.mark.parametrize('kind,target',[('delay','T01'),('signal','section-0'),('closure','section-0')])
def test_each_incident_automatically_installs_a_valid_plan(kind,target):
    async def exercise():
        sim=Simulator()
        old=copy.deepcopy(sim.state['active_plan'])
        incident=add_incident(sim,kind,target)
        coordinator,events,saved=setup(sim,seed_solver)
        coordinator.request('incident',True)
        assert sim.replanning and sim.state['awaiting_plan']
        await coordinator.task
        plan=sim.state['active_plan']
        assert plan['id']!=old['id'] and not validate_plan(sim.state,plan)
        assert not sim.state['awaiting_plan'] and not sim.replanning
        assert sim.state['replan_status']['status']=='applied'
        assert sim.state['replan_status']['comparison']['after']['conflicts']==0
        assert not sim.state['running']  # Automatic application must not unpause the user.
        assert events[-1][0]=='plan.applied' and events[-1][1]['automatic']
        assert len(saved)==1
        relevant=[m for m in plan['movements'] if
                  ((m['train_id']=='T01' and m['leg']==0) if kind=='delay' else m['section_id']==target)]
        assert all(m['start_s']>=incident['end_s'] for m in relevant)
    asyncio.run(exercise())


def test_manual_review_keeps_hold_until_application():
    async def exercise():
        sim=Simulator();old=sim.state['active_plan']['id']
        add_incident(sim)
        coordinator,events,_=setup(sim,seed_solver)
        coordinator.request('incident',False)
        await coordinator.task
        assert sim.state['active_plan']['id']==old and sim.state['awaiting_plan']
        assert sim.state['replan_status']['status']=='review' and sim.plans
        assert not any(kind=='plan.applied' for kind,_ in events)
        coordinator.install(next(iter(sim.plans.values())))
        assert not sim.state['awaiting_plan'] and sim.state['replan_status']['status']=='applied'
        assert not sim.state['replan_status']['automatic']
    asyncio.run(exercise())


def test_turning_off_automatic_mode_during_calculation_prevents_installation():
    async def exercise():
        sim=Simulator();add_incident(sim)
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def solve(snapshot):
            calls.append(snapshot)
            if len(calls)==1:
                entered.set();await release.wait()
            return await seed_solver(snapshot)
        coordinator,events,_=setup(sim,solve)
        coordinator.request('incident',True)
        await asyncio.wait_for(entered.wait(),2)
        sim.state['replanning_options']['auto_apply']=False
        coordinator.request('options',False)
        release.set();await coordinator.task
        assert sim.state['replan_status']['status']=='review' and sim.state['awaiting_plan']
        assert not any(kind=='plan.applied' for kind,_ in events)
    asyncio.run(exercise())


def test_new_incident_discards_old_result_and_solves_latest_inputs():
    async def exercise():
        sim=Simulator();add_incident(sim)
        entered=asyncio.Event();release=asyncio.Event();snapshots=[]
        async def solve(snapshot):
            snapshots.append(snapshot)
            if len(snapshots)==1:
                entered.set();await release.wait()
            return await seed_solver(snapshot)
        coordinator,events,saved=setup(sim,solve)
        coordinator.request('incident',True)
        await asyncio.wait_for(entered.wait(),2)
        add_incident(sim,'delay','T03',1200)
        latest=coordinator.request('incident',True)['job_id']
        release.set();await coordinator.task
        assert len(snapshots)==2 and len(saved)==1
        assert len(snapshots[-1]['incidents'])==2
        assert saved[0]['constraint_version']==sim.state['constraint_version']
        assert sim.state['replan_status']['job_id']==latest
        assert sum(kind=='plan.applied' for kind,_ in events)==1
        assert validate_plan(sim.state,sim.state['active_plan'])==[]
    asyncio.run(exercise())


@pytest.mark.parametrize('request_after_reset',[False,True])
def test_reset_invalidates_inflight_work(request_after_reset):
    async def exercise():
        sim=Simulator();add_incident(sim)
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def solve(snapshot):
            calls.append(snapshot)
            if len(calls)==1:
                entered.set();await release.wait()
            return await seed_solver(snapshot)
        coordinator,events,saved=setup(sim,solve)
        coordinator.request('incident',True)
        await asyncio.wait_for(entered.wait(),2)
        epoch=sim.state['epoch']
        coordinator.invalidate();sim.reset()
        if request_after_reset:
            add_incident(sim,'signal','section-1')
            coordinator.request('incident',True)
        release.set();await coordinator.task
        assert sim.state['epoch']!=epoch
        assert len(saved)==int(request_after_reset)
        assert all(plan['epoch']==sim.state['epoch'] for plan in saved)
        assert sim.state['replan_status']['status']==('applied' if request_after_reset else 'idle')
        assert not sim.replanning
    asyncio.run(exercise())


@pytest.mark.parametrize('failure',['exception','invalid','empty','malformed'])
def test_failure_retains_plan_and_holds_departures_then_can_retry(failure):
    async def exercise():
        sim=Simulator();old=copy.deepcopy(sim.state['active_plan']);add_incident(sim)
        async def failing(snapshot):
            if failure=='exception':raise RuntimeError('Solver unavailable')
            if failure=='empty':return {'plans':[]}
            if failure=='malformed':return {'plans':[None]}
            plan=shifted_seed(snapshot);plan['movements'][0]['release_s']=0
            return {'plans':[plan]}
        coordinator,events,_=setup(sim,failing)
        coordinator.request('incident',True);await coordinator.task
        assert sim.state['active_plan']==old and sim.state['awaiting_plan']
        assert sim.state['replan_status']['status']=='failed' and not sim.replanning
        assert not any(kind=='plan.applied' for kind,_ in events)
        sim.state['running']=True;sim.tick(60)
        assert sim.state['committed']==[]
        coordinator.solve=seed_solver
        coordinator.request('retry',True);await coordinator.task
        assert sim.state['replan_status']['status']=='applied' and not sim.state['awaiting_plan']
    asyncio.run(exercise())


@pytest.mark.parametrize('always_stale',[False,True])
def test_running_clock_retries_stale_result_with_a_bound(always_stale):
    async def exercise():
        sim=Simulator();add_incident(sim);sim.state.update(running=True,speed=60)
        calls=[]
        async def solve(snapshot):
            calls.append(snapshot)
            result=await seed_solver(snapshot)
            if always_stale or len(calls)==1:sim.tick(2000)
            return result
        coordinator,events,_=setup(sim,solve)
        coordinator.request('incident',True);await coordinator.task
        assert len(calls)==(3 if always_stale else 2)
        assert sim.state['awaiting_plan']==always_stale
        assert sim.state['replan_status']['status']==('failed' if always_stale else 'applied')
        assert not sim.replanning
    asyncio.run(exercise())


def test_moving_trains_clear_closed_section_without_changing_started_movements():
    async def exercise():
        sim=Simulator();sim.state['running']=True;sim.tick(60)
        committed=[copy.deepcopy(m) for m in sim.state['active_plan']['movements'] if started(sim.state,m)]
        assert committed
        closure=add_incident(sim,'closure','section-0',1200)
        async def solve(snapshot):
            # The simulation continues while a separate worker calculates.
            sim.tick(60)
            assert sim.state['committed']==snapshot['committed']
            return build_plans(snapshot)
        coordinator,_,_=setup(sim,solve)
        coordinator.request('incident',True);await coordinator.task
        assert sim.state['replan_status']['status']=='applied'
        new=sim.state['active_plan']
        assert all(m in new['movements'] for m in committed)
        assert not validate_plan(sim.state,new)
        assert all(m['start_s']>=closure['end_s'] for m in new['movements'] if m['section_id']=='section-0' and not started(sim.state,m))
    asyncio.run(exercise())
