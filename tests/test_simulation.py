import hashlib
import json
import math
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend import ato, dispatch
from backend.blocks import block_edges
from backend.engine import Engine
from backend.history import History
from backend.models import IncidentSpec, Settings, TrainSpec
from backend.network import ROOT, hav


def train(engine,tid='T1',**kwargs):
    defaults=dict(id=tid,name=tid,origin=engine.demo['origin'],destination=engine.demo['destination'],scheduled_arrival_s=1800)
    return TrainSpec(**{**defaults,**kwargs})


def run_checked(e,seconds):
    for _ in range(int(seconds/5)):
        e.advance(5)
        assert not e.invariant_errors()
        for t in e.trains.values():
            assert 0<=t.x<=t.total
            idx,arc,offset=e.network.locate(t.route,t.x)
            p=e.network.position(arc,offset)
            assert len(p)==2 and all(math.isfinite(v) for v in p)
            assert arc[0] in e.network.allowed


def test_map_preserved_byte_for_byte():
    baseline=json.loads((ROOT/'docs/map-preservation.json').read_text())
    assert {f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in baseline}==baseline


def test_routing_does_not_use_synthetic_links_or_reversals(network):
    d=network.find_demo()
    for name in ('siding_route','bypass_route'):
        route=d[name]
        assert all(e in network.allowed and not network.ways[network.edges[e]['way']]['synthetic'] for e,_ in route)
        assert all(network.compatible(a,b) for a,b in zip(route,route[1:]))
    assert d['siding_edge'] not in [e for e,_ in d['bypass_route']]


def test_opposing_trains_pass_at_existing_siding(engine):
    engine.load_demo('passing')
    run_checked(engine,1300)
    local,express=engine.trains.values()
    assert local.state=='waiting'
    assert dispatch.resources(engine,local,physical=True)=={f'e:{engine.demo["siding_edge"]}'}
    assert express.speed>0
    assert engine.demo['siding_edge'] not in [e for e,_ in express.route]
    run_checked(engine,1100)
    assert all(t.state=='completed' for t in engine.trains.values())
    assert not engine.deadlock


def test_overtaking_uses_existing_bypass(engine):
    engine.load_demo('overtaking')
    run_checked(engine,3500)
    low,high=engine.trains.values()
    assert high.arrivals[-1]['time']<low.arrivals[-1]['time']
    assert low.arrivals[0]['label']==f'Siding E{engine.demo["siding_edge"]}'
    assert high.state==low.state=='completed'


def test_importance_preference_and_fairness(engine):
    engine.load_demo('priority')
    low,high=engine.trains.values()
    assert high.authority is not None and low.authority is None
    # An importance edit cannot revoke the other train's committed route.
    before=high.authority
    low.spec.importance=10; engine.replan('importance changed')
    assert high.authority==before and low.authority is None
    run_checked(engine,3800)
    assert low.actual_departure is not None and low.state=='completed'
    assert high.actual_departure<low.actual_departure


def test_starvation_gate_overrides_score_when_feasible(engine):
    low=engine.add_train(train(engine,'LOW',importance=1),replan=False)
    high=engine.add_train(train(engine,'HIGH',importance=10),replan=False)
    low.waiting=engine.config.starvation_s+1
    engine.replan('fairness test')
    assert low.authority is not None and high.authority is None
    assert 'FIFO' in low.reason


def test_siding_usable_length_validation(engine):
    with pytest.raises(ValueError,match='usable siding length'):
        engine.add_train(train(engine,via_siding=engine.demo['siding_edge'],length_m=980))
    t=engine.add_train(train(engine,via_siding=engine.demo['siding_edge'],length_m=900))
    assert t.spec.length_m+50<engine.demo['siding_length_m']


def test_closure_with_and_without_alternate_route(engine):
    t=engine.add_train(train(engine),replan=False)
    bypass=engine.demo['bypass_route'][2][0]
    engine.add_incidents([IncidentSpec(kind='track_closure',asset_type='edge',asset_id=str(bypass),duration_s=600)])
    assert bypass not in [a[0] for a in t.route]
    assert engine.demo['siding_edge'] in [a[0] for a in t.route]
    assert any(len(a['actions'])==2 for a in engine.alternatives)
    engine.reset()
    t=engine.add_train(train(engine),replan=False)
    approach=engine.demo['bypass_route'][0][0]
    engine.add_incidents([IncidentSpec(kind='track_closure',asset_type='edge',asset_id=str(approach),duration_s=50)])
    assert t.authority is None and not t.launched
    run_checked(engine,100)
    assert t.x>0


def test_signal_failure_blocks_entry_then_recovers(engine):
    t=engine.add_train(train(engine),replan=False)
    arc=t.route[0]
    engine.add_incidents([IncidentSpec(id='FAILED',kind='signal_failure',asset_type='signal',asset_id=f'SIG-{arc[0]}-{arc[1]}',duration_s=None)])
    assert t.authority is None
    assert engine.signals[f'SIG-{arc[0]}-{arc[1]}']['aspect']=='red'
    run_checked(engine,20)
    assert t.x==0
    engine.clear_incident('FAILED')
    run_checked(engine,20)
    assert t.x>0


def test_incident_inside_section_brakes_without_releasing_occupancy(engine):
    t=engine.add_train(train(engine))
    engine.advance(100)
    eid=engine.network.locate(t.route,t.x)[1][0]
    old_speed=t.speed; before=t.x; authority=t.authority
    engine.add_incidents([IncidentSpec(id='BLOCK',kind='track_closure',asset_type='edge',asset_id=str(eid),duration_s=None)])
    engine.advance(.2)
    assert old_speed-t.speed <= t.spec.braking_mps2*.2+1e-8
    assert t.x>before
    engine.advance(60)
    assert t.speed==0 and t.authority==authority
    assert eid in block_edges(dispatch.resources(engine,t,physical=True))
    stopped=t.x
    engine.clear_incident('BLOCK'); engine.advance(10)
    assert t.x>stopped


def test_train_tail_retains_section_and_switch(engine):
    t=engine.add_train(train(engine))
    n=engine.network; e=t.route[0][0]; boundary=n.edges[e]['length_m']
    t.x=boundary+50
    t.authority=t.x
    engine.replan('consist straddles original edge boundary')
    assert e in block_edges(dispatch.resources(engine,t,physical=True))
    assert f'v:{n.endpoint(t.route[0])}' in dispatch.resources(engine,t)
    t.x=boundary+t.spec.length_m+engine.config.clearance_m+.1
    assert e not in block_edges(dispatch.resources(engine,t,physical=True))
    assert f'v:{n.endpoint(t.route[0])}' not in dispatch.resources(engine,t)


def test_acceleration_braking_speed_limits_and_energy(engine):
    t=engine.add_train(train(engine,scheduled_arrival_s=None))
    prior=0
    for _ in range(300):
        engine.advance(.2)
        assert t.speed-prior<=t.spec.acceleration_mps2*.2+1e-6
        assert prior-t.speed<=t.spec.braking_mps2*.2+1e-6
        assert t.speed*3.6<=t.spec.max_speed_kmh+1e-6
        prior=t.speed
    assert t.energy>0
    eid=t.route[0][0]
    engine.add_incidents([IncidentSpec(kind='speed_restriction',asset_type='edge',asset_id=str(eid),speed_kmh=10,duration_s=300)])
    engine.advance(60)
    assert t.speed*3.6<=10.01
    assert ato.profile(engine,t)['recommended_kmh']<=10.01


def test_timetable_dwell_and_ato_reacts(engine):
    intermediate=engine.network.endpoint(engine.demo['bypass_route'][1])
    t=engine.add_train(train(engine,stops=[{'vertex':intermediate,'dwell_s':45,'scheduled_arrival_s':800}],scheduled_arrival_s=2000))
    run_checked(engine,850)
    assert t.arrivals and t.arrivals[0]['label']==engine.network.label(intermediate)
    arrival=t.arrivals[0]['time']
    assert arrival+45 <=engine.sim_time or t.state=='dwelling'
    before=ato.profile(engine,t)['recommended_kmh']
    t.spec.scheduled_arrival_s=10000;t.stop_points[-1]['scheduled']=10000
    after=ato.profile(engine,t)['recommended_kmh']
    assert after<=before


def test_ten_incident_batch_validation_performance_and_recovery(engine):
    engine.load_demo('passing'); engine.advance(60)
    specs=[IncidentSpec(id=f'I-{i}',kind='train_delay',asset_type='train',asset_id='KZ-101',duration_s=20+i) for i in range(10)]
    start=time.perf_counter(); engine.add_incidents(specs)
    elapsed=time.perf_counter()-start
    assert elapsed<5
    assert len(engine.incidents)==10
    version=engine.plan_version
    engine.advance(80)
    assert engine.plan_version>version and all(i['status']=='cleared' for i in engine.incidents.values())
    assert engine.trains['KZ-101'].speed>0
    assert not engine.invariant_errors()
    invalid=[IncidentSpec(id='OK',kind='train_delay',asset_type='train',asset_id='KZ-101'),IncidentSpec(id='BAD',kind='train_delay',asset_type='train',asset_id='UNKNOWN')]
    with pytest.raises(ValueError):
        engine.add_incidents(invalid)
    assert 'OK' not in engine.incidents


def test_seeded_random_incidents_reproduce(network):
    outcomes=[]
    for _ in range(2):
        e=Engine(network,config=Settings(random_incidents_per_hour=120,random_seed=7))
        e.add_train(train(e));e.advance(200)
        outcomes.append([(i['id'],i['kind'],i['start_s'],i['duration_s']) for i in e.incidents.values()])
    assert outcomes[0] and outcomes[0]==outcomes[1]


def test_replay_contains_signals_incidents_decisions_and_is_read_only(engine):
    engine.load_demo(); engine.advance(20)
    snapshot=engine.snapshot(); version=engine.plan_version
    frames=engine.history.replay(0,20)
    assert len(frames)>=19
    assert {'trains','signals','switches','incidents','plan_version','events'}<=frames[0].keys()
    frames[0]['trains'][0]['position'][0]=0
    assert engine.snapshot()['trains'][0]['position']==snapshot['trains'][0]['position']
    assert engine.plan_version==version


def test_quality_measured_and_thresholds_valid(engine):
    assert engine.quality.result(engine)['score'] is None
    t=engine.add_train(train(engine,scheduled_arrival_s=100))
    engine.advance(50)
    result=engine.quality.result(engine)
    assert 0<=result['score']<=100
    assert result['factors']['schedule']['value']<100
    assert abs(sum(f['contribution'] for f in result['factors'].values())-result['score'])<.11
    assert result['samples']>0
    with pytest.raises(ValidationError):
        Settings(attention_threshold=90,normal_threshold=80)


def test_persisted_history_and_config(tmp_path):
    db=tmp_path/'history.sqlite3'
    h=History(db); h.log(5,'incident','test',actor='test',before=1,after=2);h.save_settings(Settings().model_dump());h.db.close()
    h=History(db)
    assert h.query()[0]['before']==1 and h.query()[0]['after']==2
    assert h.load_settings()['retention_hours']==48


def test_input_validation(engine):
    with pytest.raises(ValidationError):
        train(engine,importance=11)
    with pytest.raises(ValidationError):
        train(engine,max_speed_kmh=float('nan'))
    engine.add_train(train(engine))
    with pytest.raises(ValueError,match='already exists'):
        engine.add_train(train(engine))
    with pytest.raises(ValueError,match='Unknown vertex'):
        engine.add_train(train(engine,'OUT',destination=999999))
