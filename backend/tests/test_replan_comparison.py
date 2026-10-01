import asyncio
import copy
import csv
import io
import json

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.metrics import metrics
from backend.app.planning import finish, shifted_seed
from backend.app.replanning import comparison
from backend.app.reports import csv_report
from backend.app.simulator import Simulator
from backend.app.speed_advice import eco_plan
from backend.app.storage import Store


async def variants(snapshot):
    first=shifted_seed(snapshot)
    movements=copy.deepcopy(first['movements'])
    for movement in movements:
        for key in ('start_s','end_s','release_s'):movement[key]+=60
    second=finish(snapshot,movements,'Later candidate','heuristic')
    assert second is not None
    return {'plans':[first,second]}


async def finish_job():
    await asyncio.wait_for(asyncio.shield(main.replanner.task),5)


def test_comparison_evaluates_both_plans_at_same_conditions_without_mutation():
    sim=Simulator()
    sim.state['incidents']=[{'id':'one','kind':'delay','target_id':'T01','leg':0,'start_s':0,'end_s':600}]
    sim.state['constraint_version']=1
    sim.state['awaiting_plan']=True
    plan=shifted_seed(sim.state)
    plan['calculation_s']=1.25
    before=copy.deepcopy(sim.state)
    change=comparison(sim.state,plan)
    assert sim.state==before
    assert change['basis']=='forecast_at_evaluation' and change['evaluated_at_s']==0
    assert change['epoch']==sim.state['epoch'] and change['constraint_version']==1
    assert change['before']==metrics(sim.state,sim.state['active_plan'])
    assert change['after']==metrics(sim.state,plan)
    assert change['before']['quality_signature']==change['after']['quality_signature']
    assert change['before']['conflicts']>0 and change['after']['conflicts']==0
    assert not change['before']['forecast_valid'] and change['after']['forecast_valid']
    assert change['before']['total_delay_s']<change['after']['total_delay_s']
    assert change['calculation_s']==1.25
    # A subsequent comparison uses the most recently active plan, not the baseline.
    sim.state['active_plan']=plan
    sim.state['incidents'][0]['end_s']=1200
    next_plan=shifted_seed(sim.state)
    later=comparison(sim.state,next_plan)
    assert later['old_plan_id']==plan['id']!=sim.state['baseline']['id']
    assert later['before']['total_delay_s']==change['after']['total_delay_s']


def test_economy_arrival_changes_are_not_lost_when_departures_stay_unchanged(tmp_path):
    sim=Simulator()
    proposal=eco_plan(sim.state,'T05')['plan']
    change=comparison(sim.state,proposal,basis='forecast_at_application')
    assert change['changes']
    assert any(m['before_s']==m['after_s'] and m['before_arrival_s']!=m['after_arrival_s'] for m in change['changes'])
    old={(m['train_id'],m['leg']):m for m in sim.state['active_plan']['movements']}
    new={(m['train_id'],m['leg']):m for m in proposal['movements']}
    expected={key for key in old if any(old[key][field]!=new[key][field] for field in ('start_s','end_s','release_s'))}
    assert {(m['train_id'],m['leg']) for m in change['changes']}==expected
    store=Store(f'sqlite:///{tmp_path / "economy.sqlite"}')
    try:
        store.save('snapshot',sim.state['epoch'],0,sim.snapshot())
        store.save('event',sim.state['epoch'],0,{'type':'plan.applied','payload':{'comparison':change}})
        rows=list(csv.DictReader(io.StringIO(''.join(csv_report(store,store.archive_window(sim.state['epoch']))).lstrip('\ufeff'))))
        moves=[r for r in rows if r['record_type']=='replan_movement']
        assert {'arrival','tail_release'}.issubset({r['metric'] for r in moves})
        assert all(float(r['change'])==float(r['after'])-float(r['before']) for r in moves)
        assert all(float(r['change'])!=0 for r in moves)
    finally:
        store.engine.dispose()


def test_preservation_count_does_not_claim_a_changed_started_movement_is_preserved():
    sim=Simulator();sim.state['running']=True;sim.tick(60)
    candidate=copy.deepcopy(sim.state['active_plan'])
    started=next(m for m in candidate['movements'] if m['start_s']==0)
    started['end_s']+=1
    candidate['id']='changed-started-movement'
    change=comparison(sim.state,candidate)
    assert change['committed_total']==2
    assert change['committed_preserved']==1
    assert not change['after']['forecast_valid']


def test_selected_candidate_api_application_archive_and_stale_responses(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "comparison.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',variants)
    with TestClient(main.app) as client:
        assert client.put('/api/replanning',json={'auto_apply':False,'policy':'balanced'}).status_code==200
        client.post('/api/incidents',json={'kind':'delay','target_id':'T01','duration_s':600})
        client.portal.call(finish_job)
        state=client.get('/api/state').json()
        plans=client.get('/api/plans').json()['plans']
        assert len(plans)==2 and state['replan_status']['status']=='review'
        other=next(p for p in plans if p['id']!=state['replan_status']['recommended_plan_id'])
        url=f'/api/plans/{other["id"]}/comparison'
        before=copy.deepcopy(main.sim.state)
        response=client.get(url)
        assert response.status_code==200,response.text
        data=response.json()
        assert main.sim.state==before
        assert data['plan_id']==data['comparison']['new_plan_id']==other['id']
        assert data['comparison']['old_plan_id']==state['active_plan_id']
        assert data['comparison']['after']['total_delay_s']!=state['replan_status']['comparison']['after']['total_delay_s']
        assert data['applicable']
        assert client.get('/api/plans/missing/comparison').status_code==404
        main.sim.plans[other['id']]['constraint_version']-=1
        assert client.get(url).status_code==409
        main.sim.plans[other['id']]['constraint_version']+=1
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.get(url).status_code==401
        monkeypatch.setenv('VIEWER_PASSWORD','viewer')
        client.post('/api/auth/login',json={'role':'viewer','password':'viewer'})
        assert client.get(url).status_code==200
        assert client.post(f'/api/plans/{other["id"]}/apply').status_code==403
        monkeypatch.setattr(main,'demo_mode',True)
        assert client.post(f'/api/plans/{other["id"]}/apply').status_code==200
        applied=client.get('/api/state').json()
        change=applied['replan_status']['comparison']
        assert change=={**data['comparison'],'basis':'forecast_at_application'}
        assert applied['active_plan_id']==other['id']
        assert client.get(url).status_code==404
        window=client.get('/api/history/window').json()
        replay_url=f'/api/history/snapshots/{window["frames"][-1]["id"]}?epoch={applied["epoch"]}'
        archived=client.get(replay_url).json()
        assert archived['replan_status']['comparison']==change
        params={'epoch':applied['epoch'],'to':window['to_s'],'through_id':window['through_id']}
        report=client.get('/api/reports/history.csv',params=params)
        assert report.status_code==200
        rows=list(csv.DictReader(io.StringIO(report.text.lstrip('\ufeff'))))
        quality=next(r for r in rows if r['record_type']=='replan' and r['metric']=='index')
        assert float(quality['after'])==change['after']['index']
        assert json.loads(quality['details'])['calculation_s']==change['calculation_s']
        settings=client.get('/api/settings').json()
        settings['quality_formula']='weighted_geometric'
        client.put('/api/settings',json=settings)
        assert client.get(replay_url).json()==archived
        assert client.get('/api/state').json()['replan_status']['comparison']==change
        client.post('/api/simulation/reset')
        assert client.get(replay_url).json()==archived


def test_preview_reports_clock_invalidity_without_mutating_or_applying_candidate(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "clock.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',variants)
    with TestClient(main.app) as client:
        client.post('/api/replan')
        client.portal.call(finish_job)
        candidate=next(iter(main.sim.plans.values()))
        main.sim.state['sim_time_s']=100000
        before=copy.deepcopy(main.sim.state)
        response=client.get(f'/api/plans/{candidate["id"]}/comparison')
        assert response.status_code==200
        assert not response.json()['applicable']
        assert response.json()['comparison']['after']['conflicts']>0
        assert main.sim.state==before
