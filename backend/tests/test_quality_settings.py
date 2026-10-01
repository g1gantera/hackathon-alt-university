import asyncio
import copy
import csv
import io
import json
import math

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import main
from backend.app.metrics import metrics
from backend.app.planning import shifted_seed
from backend.app.quality_config import (
    DEFAULT_SETTINGS, Settings, canonical_settings, combine_scores, quality_assessment, quality_weights,
)
from backend.app.simulator import Simulator


def weights(**values):
    return {key:values.get(key,0) for key in ('schedule','energy','capacity','conflicts','arrival_accuracy')}


def test_default_and_legacy_settings_preserve_the_original_weight_split():
    expected=weights(schedule=.42,energy=.18,capacity=.2,conflicts=.1,arrival_accuracy=.1)
    assert quality_weights(DEFAULT_SETTINGS)==pytest.approx(expected)
    assert quality_weights(canonical_settings(Settings().model_dump()))==pytest.approx(expected)
    legacy=canonical_settings(Settings(delay_weight=1,energy_weight=0).model_dump())
    assert quality_weights(legacy)==weights(schedule=.6,capacity=.2,conflicts=.1,arrival_accuracy=.1)
    # Explicit five-component weights take precedence over legacy fields.
    legacy.update(quality_weights=weights(capacity=8),delay_weight=0)
    assert quality_weights(legacy)==weights(capacity=1)
    assert quality_weights({'quality_weights':weights(schedule=2,capacity=6)})==quality_weights({'quality_weights':weights(schedule=20,capacity=60)})


def test_aggregation_losses_reconcile_and_disabled_zero_does_not_break_geometric_mean():
    scores=weights(schedule=1,energy=0,capacity=.8,conflicts=1,arrival_accuracy=1)
    normalized=weights(schedule=.5,capacity=.5)
    mean,loss=combine_scores(scores,normalized,'weighted_mean')
    assert mean==90 and sum(loss.values())==pytest.approx(10)
    geometric,loss=combine_scores(scores,normalized,'weighted_geometric')
    assert geometric==pytest.approx(100*math.sqrt(.8))
    assert 100-sum(loss.values())==pytest.approx(geometric)
    assert loss['energy']==0 and loss['capacity']>10
    scores.update(capacity=0)
    zero,loss=combine_scores(scores,normalized,'weighted_geometric')
    assert zero==0 and loss==weights(capacity=100)
    scores.update(schedule=0)
    assert combine_scores(scores,normalized,'weighted_geometric')==(0,weights(schedule=50,capacity=50))


@pytest.mark.parametrize('index,expected',[(95,'on_track'),(94.9,'attention'),(80,'attention'),(79.9,'disrupted')])
def test_assessment_threshold_boundaries(index,expected):
    assert quality_assessment(index,{'quality_threshold_normal':95,'quality_threshold_attention':80})==expected


@pytest.mark.parametrize('change',[
    {'quality_weights':weights()}, {'quality_weights':weights(capacity=-1)},
    {'quality_weights':weights(capacity=1001)}, {'quality_weights':weights(capacity=True)},
    {'quality_weights':weights(capacity='20')}, {'quality_weights':weights(capacity=float('nan'))},
    {'quality_weights':weights(capacity=float('inf'))}, {'quality_formula':'eval'},
    {'quality_threshold_normal':70,'quality_threshold_attention':70},
    {'quality_threshold_normal':0}, {'quality_threshold_attention':-1},
    {'quality_threshold_normal':101}, {'conflict_penalty':0}, {'conflict_penalty':float('inf')},
    {'unknown_setting':1}, {'quality_weights':{'unknown':1}},
])
def test_invalid_configuration_is_rejected(change):
    with pytest.raises(ValidationError):Settings(**{**DEFAULT_SETTINGS,**change})


def test_metrics_use_runtime_settings_and_round_before_assessment():
    sim=Simulator()
    original=sim.snapshot()['metrics']
    sim.state['settings']=canonical_settings(DEFAULT_SETTINGS)
    assert sim.snapshot()['metrics']['quality_signature']==original['quality_signature']
    settings=sim.state['settings']
    settings['quality_weights']=weights(conflicts=100)
    settings['conflict_penalty']=.25
    errors=[{'code':'occupancy'}]
    score=metrics(sim.state,sim.state['active_plan'],forecast=False,violations=errors)
    assert score['index']==80 and score['components']['conflicts']['loss_points']==20
    settings['conflict_penalty']=100/89.96-1
    score=metrics(sim.state,sim.state['active_plan'],forecast=False,violations=errors)
    assert score['index']==90 and score['assessment']=='on_track'
    signature=score['quality_signature']
    settings['quality_threshold_normal']=91
    changed=metrics(sim.state,sim.state['active_plan'],forecast=False,violations=errors)
    assert changed['index']==score['index'] and changed['assessment']=='attention'
    assert changed['quality_signature']!=signature


async def quick_solver(state):
    return {'plans':[shifted_seed(state)]}


async def finish_job():
    await asyncio.wait_for(asyncio.shield(main.replanner.task),5)


def test_runtime_settings_publish_archive_export_and_reset(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "settings.sqlite"}')
    monkeypatch.setattr(main,'solve_snapshot',quick_solver)
    with TestClient(main.app) as client:
        original=client.get('/api/state').json()
        defaults=client.get('/api/settings').json()
        response=client.put('/api/settings',json=defaults)
        assert response.status_code==200,response.text
        assert client.get('/api/state').json()['metrics']['quality_signature']==original['metrics']['quality_signature']
        assert client.post('/api/incidents',json={'kind':'closure','target_id':'section-1','duration_s':600}).status_code==200
        client.portal.call(finish_job)
        closed=client.get('/api/state').json()
        assert closed['metrics']['index']==96
        window=client.get('/api/history/window').json()
        frame_id=window['frames'][-1]['id']
        replay_url=f'/api/history/snapshots/{frame_id}?epoch={closed["epoch"]}'
        archived=client.get(replay_url).json()
        before=copy.deepcopy(main.sim.state)
        for invalid in ({'quality_weights':weights()},{'quality_formula':'unknown'},{'quality_threshold_normal':50}):
            assert client.put('/api/settings',json={**defaults,**invalid}).status_code==422
            assert main.sim.state==before
        settings={**defaults,'quality_weights':weights(capacity=100),'quality_threshold_normal':85,'quality_threshold_attention':75}
        # Settings invalidate previously calculated candidate plans.
        main.sim.plans['previous']=copy.deepcopy(main.sim.state['active_plan'])
        with client.websocket_connect('/ws') as ws:
            ws.receive_json()
            assert client.put('/api/settings',json=settings).status_code==200
            while True:
                event=ws.receive_json()
                if event['type']=='state.updated' and event['payload']['constraint_version']>closed['constraint_version']:
                    current=event['payload']
                    break
        assert main.sim.plans=={}
        assert current['metrics']['index']==80 and current['metrics']['assessment']=='attention'
        assert current['metrics']['formula']['weights']==weights(capacity=1)
        assert current['metrics']['formula']['thresholds']=={'normal':85,'attention':75}
        assert client.get(replay_url).json()==archived
        assert archived['metrics']['index']==96 and archived['metrics']['formula']['thresholds']['normal']==90
        trend=client.get('/api/quality').json()['trend']['points']
        assert all(point['state_version']>=current['state_version'] for point in trend)
        assert {point['index'] for point in trend}=={80}
        settings.update(quality_weights=weights(schedule=50,capacity=50),quality_threshold_normal=90,quality_threshold_attention=70)
        client.put('/api/settings',json=settings)
        assert client.get('/api/state').json()['metrics']['index']==90
        settings['quality_formula']='weighted_geometric'
        client.put('/api/settings',json=settings)
        current=client.get('/api/state').json()['metrics']
        assert current['index']==89.4 and current['assessment']=='attention'
        settings.update(quality_threshold_attention=89.5,quality_threshold_normal=95)
        client.put('/api/settings',json=settings)
        assert client.get('/api/state').json()['metrics']['assessment']=='disrupted'
        window=client.get('/api/history/window').json()
        response=client.get('/api/reports/history.csv',params={'epoch':closed['epoch'],'to':window['to_s'],'through_id':window['through_id']})
        assert response.status_code==200,response.text
        report=response.text.lstrip('\ufeff')
        rows=list(csv.DictReader(io.StringIO(report)))
        assert {'on_track','attention','disrupted'}.issubset({r['value'] for r in rows if r['metric']=='assessment'})
        assert any(json.loads(r['details']).get('aggregation')=='weighted_geometric' for r in rows if r['record_type']=='formula')
        client.post('/api/simulation/reset')
        assert client.get('/api/settings').json()==defaults
        assert client.get(replay_url).json()==archived


def test_settings_mutation_requires_admin_and_legacy_payload_is_supported(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "roles.sqlite"}')
    with TestClient(main.app) as client:
        monkeypatch.setattr(main,'demo_mode',False)
        assert client.put('/api/settings',json={}).status_code==401
        for role in ('viewer','dispatcher','admin'):
            monkeypatch.setenv(role.upper()+'_PASSWORD','test-password')
            assert client.post('/api/auth/login',json={'role':role,'password':'test-password'}).status_code==200
            assert client.get('/api/settings').status_code==200
            before=copy.deepcopy(main.sim.state)
            response=client.put('/api/settings',json={'delay_weight':1,'energy_weight':0})
            assert response.status_code==(200 if role=='admin' else 403),response.text
            if role!='admin':assert main.sim.state==before
            else:assert response.json()['quality_weights']==weights(schedule=60,capacity=20,conflicts=10,arrival_accuracy=10)
            client.post('/api/auth/logout')
