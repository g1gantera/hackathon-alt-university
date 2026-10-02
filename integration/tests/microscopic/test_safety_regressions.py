import pytest

from backend.app.microscopic import ato, dispatch
from backend.app.microscopic.blocks import route_blocks
from backend.app.microscopic.models import IncidentSpec, TrainSpec


def service(engine, **changes):
    return TrainSpec(**{
        'id':'SAFE', 'name':'Safety regression',
        'origin':engine.demo['origin'], 'destination':engine.demo['destination'],
        **changes,
    })


def test_signal_does_not_authorize_entry_beyond_intermediate_stop(engine):
    vertex=engine.network.endpoint(engine.demo['bypass_route'][1])
    t=engine.add_train(service(engine,stops=[{'vertex':vertex,'dwell_s':30}]))
    # Seed a stopped consist near the station so its local grant reaches it.
    t.x=t.next_stop['distance']-500
    t.authority=t.x
    engine.replan('approaching intermediate stop')
    lengths=engine.network.lengths(t.route)
    index=lengths.index(t.authority)
    following=t.route[index]
    approach=t.route[index-1]
    # The junction footprint conservatively retains the following edge, but
    # that lock must not produce a proceed aspect past the stopping point.
    assert f'e:{following[0]}' in dispatch.resources(engine,t)
    assert engine.signals[f'SIG-{following[0]}-{following[1]}']['aspect']=='red'
    assert engine.signals[f'SIG-{approach[0]}-{approach[1]}']['aspect']=='yellow'


def test_closure_limits_signal_aspects_without_releasing_route(engine):
    t=engine.add_train(service(engine))
    first,closed=t.route[:2]
    approach=next(b for b in reversed(route_blocks(engine,t)) if b['edge']==first[0])
    t.x=approach['entry']-50
    t.authority=t.x
    engine.replan('approaching closure location')
    authority=t.authority
    engine.add_incidents([IncidentSpec(kind='track_closure',asset_type='edge',asset_id=str(closed[0]),duration_s=None)])
    assert t.authority==authority
    assert engine.signals[approach['signal_id']]['aspect']=='yellow'
    assert engine.signals[f'SIG-{closed[0]}-{closed[1]}']['aspect']=='red'
    # The original entry signal is behind the train, even while its clear
    # final block has a yellow approach signal at an internal boundary.
    assert engine.signals[f'SIG-{first[0]}-{first[1]}']['aspect']=='red'


def test_explicit_siding_respects_one_way_track(engine,monkeypatch):
    eid=engine.demo['siding_edge']
    way=engine.network.ways[engine.network.edges[eid]['way']]
    monkeypatch.setitem(way,'tags',{**way['tags'],'oneway':'yes'})
    with pytest.raises(ValueError,match='direction-compatible'):
        engine.add_train(service(engine,origin=engine.demo['destination'],destination=engine.demo['origin'],via_siding=eid))


@pytest.mark.parametrize('braking',[.1,.7,2])
@pytest.mark.parametrize('dt',[.03,.2])
def test_arrival_respects_braking_and_reaches_rest(engine,braking,dt):
    t=engine.add_train(service(engine,braking_mps2=braking))
    t.x=t.total-5
    t.authority=t.x
    engine.replan('stopped consist near terminal')
    for _ in range(2000):
        before=t.speed
        engine.advance(dt)
        assert before-t.speed<=braking*dt+1e-8
        assert t.speed-before<=t.spec.acceleration_mps2*dt+1e-8
        if t.state=='arrived':
            break
    assert t.state=='arrived'
    assert t.x==t.total and t.speed==0
    assert len(t.arrivals)==1


def test_late_short_block_closure_brakes_to_rest_after_overrun(engine):
    t=engine.add_train(service(engine))
    lengths=engine.network.lengths(t.route)
    closed=t.route[1][0]
    t.x=lengths[1]-1
    t.authority=t.x
    engine.replan('consist near short block with forward braking grant')
    t.speed=20
    authority=t.authority
    engine.add_incidents([IncidentSpec(id='LATE',kind='track_closure',asset_type='edge',asset_id=str(closed),duration_s=None)])
    for _ in range(160):
        before=t.speed
        engine.advance(.2)
        assert 0<=before-t.speed<=t.spec.braking_mps2*.2+1e-8
        assert t.authority==authority
        assert not engine.invariant_errors()
    # An impossible instantaneous stop is not fabricated. Braking must remain
    # latched after the whole consist has passed this short, newly closed edge.
    assert t.x-t.spec.length_m>lengths[2]
    assert t.speed==0 and t.state=='incident_hold'
    assert engine.affected_trains(engine.incidents['LATE'])==[t.spec.id]
    engine.clear_incident('LATE')
    engine.advance(1)
    assert t.speed>0 and not t.emergency_incidents


def test_closure_under_train_rear_lets_consist_clear(engine):
    t=engine.add_train(service(engine))
    end=engine.network.lengths(t.route)[1]
    t.x=end+20
    t.authority=t.x
    engine.replan('head has left the first edge; rear still on it')
    t.speed=10
    engine.add_incidents([IncidentSpec(id='REAR',kind='track_closure',asset_type='edge',asset_id=str(t.route[0][0]),duration_s=None)])
    # The train is affected while its rear remains, but it must not stand
    # inside the closed section until clearance: it keeps moving to vacate it.
    assert engine.affected_trains(engine.incidents['REAR'])==[t.spec.id]
    engine.advance(.2)
    assert t.speed>=10-1e-9
    assert 'REAR' not in t.emergency_incidents
    engine.advance(30)
    assert t.x-t.spec.length_m>end
    assert t.state=='running' and not engine.invariant_errors()


def test_restriction_holds_until_rear_leaves_section(engine):
    t=engine.add_train(service(engine,length_m=1000,scheduled_arrival_s=None))
    lengths=engine.network.lengths(t.route)
    restricted=next(i for i,arc in enumerate(t.route) if engine.network.edges[arc[0]]['length_m']>900 and i>0)
    engine.add_incidents([IncidentSpec(kind='speed_restriction',asset_type='edge',asset_id=str(t.route[restricted][0]),speed_kmh=20,duration_s=None)])
    start,end=lengths[restricted],lengths[restricted+1]
    inside=0
    for _ in range(20000):
        engine.advance(.2)
        if t.x>start and t.x-t.spec.length_m<end:
            inside+=1
            assert t.speed*3.6<=20+1e-6
        if t.x-t.spec.length_m>end:
            break
    assert inside>0 and t.x-t.spec.length_m>end


def test_ato_profile_is_evaluated_at_future_passing_times(engine):
    t=engine.add_train(service(engine,max_speed_kmh=100,scheduled_arrival_s=1500))
    engine.advance(300)
    points=ato.profile(engine,t)['points']
    cruise=[p['recommended_kmh'] for p in points if p['distance_m']<=3000]
    # Constant timetable pacing must not appear as a fictitious decline.
    assert max(cruise)-min(cruise)<2


def test_dense_acyclic_wait_graph_and_real_cycle():
    graph={str(i):[str(j) for j in range(i+1,80)] for i in range(80)}
    assert dispatch.wait_for_cycle(graph)==[]
    graph['79']=['78']
    assert set(dispatch.wait_for_cycle(graph))=={'78','79'}


def test_zero_arrival_target_contributes_to_dispatch_delay(engine):
    t=engine.add_train(service(engine,scheduled_arrival_s=0),replan=False)
    assert dispatch.score(engine,t)[1]['delay']==engine.config.weights.delay
    t.spec.scheduled_arrival_s=None
    assert dispatch.score(engine,t)[1]['delay']==0
