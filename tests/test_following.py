"""Following regressions on the original graph, including the reported route."""
import pytest

from backend import ato, dispatch
from backend.blocks import route_blocks
from backend.models import IncidentSpec, TrainSpec


def service(tid,**changes):
    return TrainSpec(**dict(id=tid,name=tid,origin=30979,destination=30977,**changes))


def assert_exclusive(engine):
    assert not engine.invariant_errors()
    occupied=set()
    for t in engine.trains.values():
        footprint=dispatch.resources(engine,t,physical=True)
        assert not occupied & footprint
        occupied.update(footprint)
        assert footprint<=dispatch.resources(engine,t)


@pytest.mark.parametrize('origins',[(30979,)*4,(30979,30980,30981,30982)])
def test_four_trains_follow_before_leader_arrives(engine,origins):
    trains=[engine.add_train(TrainSpec(id=f'T{i}',name='Following',origin=origin,
        destination=33999,max_speed_kmh=120)) for i,origin in enumerate(origins)]
    simultaneous=0
    for _ in range(100):
        engine.advance(5)
        assert_exclusive(engine)
        simultaneous=max(simultaneous,sum(t.speed>0 for t in trains))
        if simultaneous==4:
            break
    assert simultaneous==4
    assert all(t.actual_departure is not None and not t.arrivals for t in trains)
    assert trains[0].x<trains[0].total/4


def test_admission_requires_rear_and_junction_clearance(engine):
    lead=engine.add_train(service('LEAD',length_m=600))
    follower=engine.add_train(service('FOLLOW'))
    boundary=engine.network.lengths(lead.route)[1]
    lead.x=boundary+lead.spec.length_m+engine.config.clearance_m
    engine.replan('rear at block boundary')
    assert not follower.launched
    lead.x+=.01
    engine.replan('rear clear of block and junction')
    assert follower.launched and 0<follower.authority<follower.total
    assert_exclusive(engine)


def stopped_leader(engine):
    lead=engine.add_train(service('LEAD'))
    lead.x=5000
    engine.add_incidents([IncidentSpec(id='BREAK',kind='train_breakdown',
        asset_type='train',asset_id=lead.spec.id,duration_s=None)])
    return lead


def test_signal_wait_is_not_arrival_and_resumes_after_clearance(engine):
    lead=stopped_leader(engine)
    follower=engine.add_train(service('FOLLOW',max_speed_kmh=120,braking_mps2=.3))
    boundary=follower.authority
    assert 0<boundary<follower.total
    # Subdividing the leader's long edge allows the follower farther forward;
    # include travel at the corridor's 40 km/h approach limit plus braking.
    for _ in range(3000):
        previous=follower.speed
        engine.advance(.2)
        assert previous-follower.speed<=follower.spec.braking_mps2*.2+1e-8
        assert follower.speed-previous<=follower.spec.acceleration_mps2*.2+1e-8
        assert follower.x<=boundary
        assert_exclusive(engine)
        if follower.state=='waiting':
            break
    assert follower.state=='waiting' and follower.speed==0
    assert follower.x==boundary and follower.authority==boundary
    assert follower.stop_index==0 and not follower.arrivals
    assert follower.blockers==[lead.spec.id]
    assert 'section clearance' in follower.reason
    engine.advance(10)
    assert follower.waiting>=10
    engine.clear_incident('BREAK')
    for _ in range(100):
        engine.advance(5)
        assert_exclusive(engine)
        if follower.state=='completed':
            break
    assert lead.state==follower.state=='completed'
    assert len(follower.arrivals)==1
    assert follower.arrivals[0]['error_m']==0


def test_opposing_admission_checks_beyond_partial_authority(engine,monkeypatch):
    # Exercise following on this corridor without taking its optional detour.
    monkeypatch.setattr(engine,'try_reroute',lambda *_:False)
    lead=engine.add_train(TrainSpec(id='LEAD',name='Lead',origin=30979,destination=31070))
    lead.x=1500
    follower=engine.add_train(service('FOLLOW'))
    assert 0<follower.authority<lead.total<follower.total
    opposing=engine.add_train(TrainSpec(id='OPPOSING',name='Opposing',
        origin=30977,destination=31024,importance=10))
    entry=f'e:{opposing.route[0][0]}'
    assert all(entry not in dispatch.resources(engine,t) for t in (lead,follower))
    assert not opposing.launched and opposing.authority is None
    assert follower.spec.id in opposing.blockers
    assert 'Opposing' in opposing.reason
    for _ in range(160):
        engine.advance(5)
        assert_exclusive(engine)
        if opposing.launched:
            break
    assert opposing.launched
    assert lead.state==follower.state=='completed'


def test_partial_authority_is_retained_during_priority_change_and_incident(engine):
    stopped_leader(engine)
    follower=engine.add_train(service('FOLLOW'))
    engine.advance(30)
    authority=follower.authority
    follower.spec.importance=10
    engine.replan('importance edited')
    assert follower.authority==authority
    old_speed=follower.speed
    engine.add_incidents([IncidentSpec(id='FOLLOW-BREAK',kind='train_breakdown',
        asset_type='train',asset_id=follower.spec.id,duration_s=None)])
    engine.advance(.2)
    assert old_speed-follower.speed<=follower.spec.braking_mps2*.2+1e-8
    engine.advance(60)
    assert follower.authority==authority and follower.speed==0
    assert not follower.arrivals
    assert_exclusive(engine)
    engine.clear_incident('FOLLOW-BREAK')
    engine.advance(5)
    assert follower.speed>0


def test_signals_stop_at_partial_authority(engine):
    stopped_leader(engine)
    follower=engine.add_train(service('FOLLOW'))
    for block in route_blocks(engine,follower):
        signal=engine.signals[block['signal_id']]
        if block['entry']<follower.authority<block['exit']:
            assert signal['owner']==follower.spec.id and signal['aspect']=='yellow'
        if follower.authority<block['entry']<engine.trains['LEAD'].x:
            assert signal['aspect']=='red'


def test_timetable_pacing_uses_destination_distance(engine):
    stopped_leader(engine)
    follower=engine.add_train(service('FOLLOW',scheduled_arrival_s=600))
    expected=follower.total/(600-5)
    assert follower.authority<follower.total
    assert ato.envelope(engine,follower)==pytest.approx(expected)


def test_following_eta_uses_section_clearance_not_full_trip(engine):
    lead=engine.add_train(TrainSpec(id='LEAD',name='Lead',origin=30979,destination=33999))
    follower=engine.add_train(TrainSpec(id='FOLLOW',name='Follow',origin=30979,destination=33999))
    assert follower.blockers==[lead.spec.id]
    assert 0<engine.eta(follower)-engine.eta(lead)<300
    engine.add_incidents([IncidentSpec(id='BREAK',kind='train_breakdown',
        asset_type='train',asset_id=lead.spec.id,duration_s=None)])
    assert engine.eta(follower) is None
    engine.clear_incident('BREAK')
    assert engine.eta(follower) is not None
