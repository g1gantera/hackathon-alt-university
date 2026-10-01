import asyncio
import copy

import pytest

from backend.app.simulator import Simulator
from backend.app.timing import MAX_SPEED, ModelClock, UPDATE_INTERVAL_MS, run_periodic


def scheduled_starts(work, early_wakeup=False):
    """A fake monotonic clock avoids flaky real-time assertions in unit tests."""
    now=100.0
    starts=[]

    async def sleep(delay):
        nonlocal now,early_wakeup
        assert delay>0
        if early_wakeup:
            now+=delay/2
            early_wakeup=False
        else:
            now+=delay

    def callback():
        nonlocal now
        if len(starts)==len(work):
            raise asyncio.CancelledError
        starts.append(now-100)
        now+=work[len(starts)-1]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_periodic(callback,clock=lambda:now,sleep=sleep))
    return starts


def test_processing_time_does_not_accumulate_as_stream_drift():
    starts=scheduled_starts([.03,.08,.2,.01]*25)
    assert starts==pytest.approx([(i+1)*.5 for i in range(100)])


def test_long_stall_skips_missed_deadlines_without_catchup_burst():
    assert scheduled_starts([.03,1.6,.04,.03])==pytest.approx([.5,1,3,3.5])


def test_timer_waking_early_cannot_accelerate_the_model():
    assert scheduled_starts([.03,.04],early_wakeup=True)==pytest.approx([.5,1])


@pytest.mark.parametrize('speed',[1,7,30,60,1000,MAX_SPEED])
def test_two_updates_keep_the_selected_model_speed_exact(speed):
    clock=ModelClock()
    steps=[clock.step(epoch='run',speed=speed,running=True,held=False) for _ in range(20)]
    assert sum(steps)==10*speed
    assert all(isinstance(step,int) for step in steps)
    assert UPDATE_INTERVAL_MS==500


@pytest.mark.parametrize('change',[
    {'running':False}, {'held':True}, {'epoch':'reset'}, {'speed':8},
])
def test_clock_drops_fractional_time_across_control_changes(change):
    clock=ModelClock()
    context={'epoch':'run','speed':7,'running':True,'held':False}
    assert clock.step(**context)==3
    changed={**context,**change}
    step=clock.step(**changed)
    assert step==(0 if not changed['running'] or changed['held'] else changed['speed']//2)
    assert clock.step(**context)==3
    assert clock.step(**context)==4


def test_half_second_steps_match_existing_physics_and_finish_exactly():
    fast=Simulator()
    reference=Simulator()
    reference.state=copy.deepcopy(fast.state)
    fast.state['running']=reference.state['running']=True
    clock=ModelClock()
    for speed in (1,7,30,60,1000,MAX_SPEED):
        for _ in range(2):
            fast.tick(clock.step(epoch=fast.state['epoch'],speed=speed,
                                 running=fast.state['running'],held=fast.clock_held_for_replan))
        reference.tick(speed)
        assert fast.state==reference.state
    assert not fast.state['running']


def test_actual_replanning_hold_never_accumulates_a_jump_on_resume():
    sim=Simulator()
    sim.state.update(running=True,speed=1001,awaiting_plan=True)
    clock=ModelClock()
    for _ in range(20):
        sim.tick(clock.step(epoch=sim.state['epoch'],speed=sim.state['speed'],
                            running=sim.state['running'],held=sim.clock_held_for_replan))
    assert sim.state['sim_time_s']==0
    sim.state['awaiting_plan']=False
    sim.tick(clock.step(epoch=sim.state['epoch'],speed=sim.state['speed'],
                        running=sim.state['running'],held=sim.clock_held_for_replan))
    assert sim.state['sim_time_s']==500
