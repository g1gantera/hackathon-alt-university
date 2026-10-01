import copy

import pytest

from backend.app.metrics import metrics
from backend.app.planning import shifted_seed
from backend.app.simulator import Simulator
from backend.app.validation import validate_plan


def incident(kind='closure', section='section-1', **changes):
    return {'id':'test','kind':kind,'target_id':section,'start_s':0,'end_s':600,**changes}


def test_undisrupted_baseline_stays_at_100_as_energy_is_consumed():
    sim=Simulator()
    sim.state['running']=True
    for target in (0,600,7200,100000):
        sim.tick(target-sim.state['sim_time_s'])
        live=sim.snapshot()['metrics']
        assert live['index']==100
        assert live['conflicts']==0
        assert live['total_delay_s']==0
        assert live['reference_energy_kwh']==live['energy_kwh']
    assert live['energy_kwh']>0 and live['completed_trips']==8


@pytest.mark.parametrize('kind',['closure','signal'])
def test_blocked_section_lowers_index_immediately_even_without_affected_departures(kind):
    sim=Simulator()
    # No departure is scheduled on this middle section during the first ten minutes.
    sim.state['incidents']=[incident(kind)]
    assert not validate_plan(sim.state,sim.state['active_plan'])
    result=sim.snapshot()['metrics']
    assert result['index']==96
    assert result['total_delay_s']==0
    assert result['available_sections']==4
    assert result['components']['capacity']['score']==80
    assert result['blocked_sections']==['section-1']
    assert not sim.state['running'] and sim.state['sim_time_s']==0


def test_overlapping_incidents_count_each_section_once_and_expiry_restores_availability():
    sim=Simulator()
    sim.state['incidents']=[incident(),incident('signal'),incident(section='section-2',end_s=300),
                            incident(section='section-3',start_s=601,end_s=900)]
    assert sim.snapshot()['metrics']['available_sections']==3
    sim.state['sim_time_s']=300
    assert sim.snapshot()['metrics']['available_sections']==4
    sim.state['incidents'][0].update(end_s=300,resolved_s=300)
    assert sim.snapshot()['metrics']['available_sections']==4  # signal still blocks this section
    sim.state['sim_time_s']=600
    assert sim.snapshot()['metrics']['available_sections']==5


def test_held_trains_accumulate_delay_before_any_actual_arrival():
    sim=Simulator()
    first_end=min(m['end_s'] for m in sim.state['baseline']['movements'])
    sim.state.update(running=True,awaiting_plan=True)
    sim.tick(first_end+301)
    result=sim.snapshot()['metrics']
    expected=sum(max(0,sim.state['sim_time_s']-m['end_s']) for m in sim.state['baseline']['movements'])
    assert result['total_delay_s']==expected and expected>0
    assert result['index']<100
    assert result['on_time_pct']==0
    assert result['completed_trips']==0 and result['energy_kwh']==0
    assert all(t['status']=='waiting' for t in sim.snapshot()['trains'])
    sim.tick(60)
    assert sim.snapshot()['metrics']['total_delay_s']>expected


def test_live_conflicts_are_revalidated_and_forecasts_stay_separate():
    sim=Simulator()
    sim.state['incidents']=[incident(section='section-0',end_s=7200)]
    plan=sim.state['active_plan']
    assert plan['violations']==[]  # stale metadata must not mask the incident
    before=sim.snapshot()['metrics']
    assert before['conflicts']==len(validate_plan(sim.state,plan))>0
    assert before['components']['conflicts']['score']<100
    replacement=shifted_seed(copy.deepcopy(sim.state))
    assert replacement and not replacement['violations']
    sim.state['active_plan']=replacement
    live=sim.snapshot()['metrics']
    forecast=metrics(sim.state,replacement)
    assert live['conflicts']==0 and live['index']>before['index']
    assert live['index']==96  # closure remains active despite a valid replacement
    assert live['total_delay_s']==0 and forecast['total_delay_s']>0
    sim.state['incidents'][0].update(end_s=0,resolved_s=0)
    assert sim.snapshot()['metrics']['index']==100
    # Past delays will count later, but no predicted delay is presented as already incurred.


def test_component_weights_and_snapshot_reads_are_consistent():
    sim=Simulator()
    sim.state['settings'].update(delay_weight=1,energy_weight=0)
    original=copy.deepcopy(sim.state)
    result=sim.snapshot()['metrics']
    assert sim.state==original
    assert sum(v['weight'] for v in result['components'].values())==pytest.approx(1)
    assert result['components']['schedule']['weight']==.6
    assert result['components']['energy']['weight']==0
    assert result['index']==round(sum(v['score']*v['weight'] for v in result['components'].values()),1)
