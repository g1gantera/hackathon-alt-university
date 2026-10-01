import copy

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.capacity import capacity_metrics, union_seconds
from backend.app.metrics import metrics
from backend.app.simulator import Simulator
from backend.app.speed_advice import eco_plan
from backend.app.storage import Store


def test_no_data_is_neutral_and_baseline_remains_100_with_real_utilization():
    sim=Simulator()
    initial=sim.snapshot()['metrics']
    assert initial['quality_version']==4 and initial['index']==100
    assert initial['capacity']['window_s']==0
    assert initial['capacity']['utilization_pct'] is None
    assert initial['capacity']['delivery_pct'] is None
    for key in ('schedule','energy','arrival_accuracy'):
        assert not initial['components'][key]['observed']
    assert initial['terminal_arrivals']['on_time_pct'] is None
    sim.state['running']=True;sim.tick(600)
    current=sim.snapshot()['metrics']
    assert current['index']==100
    assert current['capacity']['utilization_pct']==40  # two of five sections occupied for all 600 seconds
    assert current['capacity']['delivery_pct']==100
    assert current['capacity']['distance_m']==current['capacity']['reference_distance_m']>0


def test_held_departures_and_capacity_drop_before_any_arrival_is_due():
    sim=Simulator();sim.state.update(running=True,awaiting_plan=True)
    sim.tick(60)
    result=sim.snapshot()['metrics']
    assert result['total_delay_s']==0  # no station arrival was expected yet
    assert result['departures']['deviation_s']==120  # two departures should have left at zero
    assert result['departures']['delay_s']==120
    assert result['components']['schedule']['score']<100
    assert result['components']['capacity']['score']==0
    assert result['capacity']['distance_m']==0<result['capacity']['reference_distance_m']
    assert not result['components']['arrival_accuracy']['observed']
    assert result['index']<80


def test_capacity_clips_tail_reservations_and_deduplicates_occupancy_and_closures():
    assert union_seconds([(-100,20),(10,80),(80,130),(20,40)],0,100)==100
    sim=Simulator();sim.state.update(running=True);sim.tick(600)
    plan=copy.deepcopy(sim.state['active_plan'])
    plan['movements'].append(copy.deepcopy(plan['movements'][0]))
    sim.state['incidents']=[{'kind':'closure','target_id':'section-0','start_s':100,'end_s':400},
                            {'kind':'signal','target_id':'section-0','start_s':200,'end_s':500},
                            {'kind':'closure','target_id':'section-0','start_s':800,'end_s':1000}]
    result=capacity_metrics(sim.state,plan)
    row=next(r for r in result['sections'] if r['section_id']=='section-0')
    assert row['occupied_s']==600 and row['utilization_pct']==100
    assert row['entry_blocked_s']==400
    assert result['entry_blocked_pct']==pytest.approx(400/(5*600)*100,abs=.01)
    assert not row['blocked_now']
    # A closure while an already-entered train clears can overlap occupancy.
    assert row['occupied_s']+row['entry_blocked_s']>result['window_s']


def test_rolling_window_is_bounded_and_does_not_reward_more_than_100_percent():
    sim=Simulator();sim.state['running']=True;sim.tick(7200)
    result=sim.snapshot()['metrics']
    capacity=result['capacity']
    assert (capacity['window_start_s'],capacity['window_end_s'],capacity['window_s'])==(6300,7200,900)
    assert 0<=capacity['score']<=100
    assert all(0<=r['utilization_pct']<=100 for r in capacity['sections'])
    assert all(0<=r['occupied_s']<=900 for r in capacity['sections'])


def test_departure_early_and_late_errors_are_separate_from_terminal_arrivals():
    sim=Simulator();plan=copy.deepcopy(sim.state['active_plan'])
    for m in plan['movements']:
        if m['train_id']=='T05':
            for key in ('start_s','end_s','release_s'):m[key]-=400
    result=metrics(sim.state,plan)
    assert result['departures']['early']==5
    assert result['departures']['deviation_s']==2000
    assert result['departures']['delay_s']==0
    assert result['terminal_arrivals']['early']==1
    assert result['terminal_arrivals']['evaluated']==8
    assert result['terminal_arrivals']['on_time_pct']==87.5
    assert result['components']['arrival_accuracy']['score']==87.5


def test_late_unfinished_terminal_arrivals_count_and_do_not_disappear_on_reopening():
    sim=Simulator();sim.state.update(running=True,awaiting_plan=True)
    deadline=min(m['end_s'] for m in sim.state['baseline']['movements'] if m['leg']==4)
    sim.tick(deadline+300)
    assert sim.snapshot()['metrics']['terminal_arrivals']['evaluated']==0
    sim.tick(1)
    before=sim.snapshot()['metrics']
    assert before['terminal_arrivals']['late']==1
    assert before['terminal_arrivals']['on_time_pct']==0
    sim.state['incidents']=[]
    after=sim.snapshot()['metrics']
    assert after['terminal_arrivals']==before['terminal_arrivals']


def test_economy_energy_saving_is_visible_without_inventing_departure_or_terminal_delay():
    sim=Simulator();plan=eco_plan(sim.state,'T05')['plan']
    forecast=metrics(sim.state,plan)
    assert forecast['energy']['saved_kwh']==pytest.approx(52.27,abs=.01)
    assert forecast['departures']['deviation_s']==0
    assert forecast['terminal_arrivals']['on_time_pct']==100
    assert forecast['index']==100  # bounded score, no reward above perfect baseline
    assert forecast['capacity']['basis']=='baseline_horizon'
    assert forecast['total_delay_s']==900  # intermediate station ETA changed, separately reported
    sim.state['active_plan']=plan;sim.state['running']=True;sim.tick(11000)
    actual=sim.snapshot()['metrics']
    assert actual['energy']['saved_kwh']>0
    assert actual['departures']['delay_s']==0
    assert actual['capacity']['delivery_pct']<100  # less distance this window, not fake benefit from occupying longer
    assert actual['energy']['saved_kwh']<forecast['energy']['saved_kwh']


def test_breakdown_reconciles_and_signatures_change_only_with_formula():
    sim=Simulator();sim.state.update(running=True,awaiting_plan=True);sim.tick(600)
    value=sim.snapshot()['metrics']
    assert sum(v['weight'] for v in value['components'].values())==pytest.approx(1)
    assert sum(v['score']*v['weight'] for v in value['components'].values())==pytest.approx(value['index'],abs=.06)
    assert 100-sum(v['loss_points'] for v in value['components'].values())==pytest.approx(value['index'],abs=.06)
    losses=[v['loss_points'] for v in value['drivers']]
    assert losses==sorted(losses,reverse=True)
    assert sum(value['conflict_summary']['by_code'].values())==value['conflicts']
    signature=value['quality_signature']
    sim.tick(1)
    assert sim.snapshot()['metrics']['quality_signature']==signature
    sim.state['settings']['arrival_tolerance_s']=60
    assert sim.snapshot()['metrics']['quality_signature']!=signature


def test_quality_history_projects_only_current_run_and_formula_and_keeps_extrema(tmp_path):
    store=Store(f'sqlite:///{tmp_path / "history.sqlite"}')
    try:
        for i in range(220):
            store.save('snapshot','one',i,{'state_version':i,'metrics':{'index':20 if i==117 else 100,'quality_signature':'v3'}})
        store.save('snapshot','other',220,{'state_version':220,'metrics':{'index':0,'quality_signature':'v3'}})
        store.save('snapshot','one',220,{'state_version':220,'metrics':{'index':0,'quality_signature':'v2'}})
        points=store.quality_history('one',0,220,'v3',max_points=20)
        assert len(points)<=20
        assert points[0]['sim_time_s']==0 and points[-1]['sim_time_s']==219
        assert min(p['index'] for p in points)==20
        assert all(set(p)=={'sim_time_s','state_version','index'} for p in points)
        assert store.quality_history('missing',0,220,'v3')==[]
    finally:
        store.engine.dispose()


def test_quality_api_is_read_only_live_consistent_and_authorized(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "quality.sqlite"}')
    with TestClient(main.app) as client:
        state=copy.deepcopy(main.sim.state)
        data=client.get('/api/quality').json()
        assert main.sim.state==state
        assert data['actual']==client.get('/api/state').json()['metrics']
        assert data['forecast']['forecast'] and data['actual']['forecast'] is False
        assert data['actual']['capacity']['window_s']==0
        assert data['forecast']['capacity']['window_s']>0
        assert data['trend']['points'][-1]['index']==100
        main.sim.state['awaiting_plan']=True
        assert client.get('/api/quality').json()['forecast'] is None
        old_epoch=data['epoch']
        client.post('/api/simulation/reset')
        reset=client.get('/api/quality').json()
        assert reset['epoch']!=old_epoch and len(reset['trend']['points'])==1
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get('/api/quality').status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','viewer')
        client.post('/api/auth/login',json={'role':'viewer','password':'viewer'})
        assert client.get('/api/quality').status_code==200
