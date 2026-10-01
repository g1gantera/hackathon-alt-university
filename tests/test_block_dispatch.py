"""Dispatcher integration on a test-only long, single-track corridor."""
import pytest

from backend import dispatch
from backend.blocks import route_blocks
from backend.engine import Engine
from backend.models import IncidentSpec, Settings, TrainSpec
from backend.network import Network


@pytest.fixture
def corridor():
    # A minimal fixture, never part of the application's immutable map. Real
    # Network routing, signals, train integration and dispatch all run on it.
    net = Network.__new__(Network)
    net.vertices = [[70, 45], [76, 45], [77.2, 45]]
    net.edges = [
        {'id': 0, 'u': 0, 'v': 1, 'length_m': 500000, 'way': 0,
         'category': 'rail', 'geometry': [net.vertices[0], net.vertices[1]]},
        {'id': 1, 'u': 1, 'v': 2, 'length_m': 100000, 'way': 1,
         'category': 'rail', 'geometry': [net.vertices[1], net.vertices[2]]},
    ]
    net.ways = [{'tags': {'maxspeed': '160'}} for _ in net.edges]
    net.adj = [[0], [0, 1], [1]]
    net.allowed = {0, 1}
    net.arc_lengths = {}
    net.stations = []
    net.station_at = {}
    net.find_demo = lambda: {'origin': 0, 'destination': 2}
    return Engine(net, config=Settings())


def service(tid, **changes):
    return TrainSpec(**{'id': tid, 'name': tid, 'origin': 0, 'destination': 2, **changes})


def assert_exclusive(engine):
    assert not engine.invariant_errors()
    claimed = set()
    for train in engine.trains.values():
        grant = dispatch.resources(engine, train)
        assert not claimed & grant
        assert dispatch.resources(engine, train, physical=True) <= grant
        claimed.update(grant)


def test_trains_500_km_apart_depart_together_in_same_direction(corridor):
    behind = corridor.add_train(service('BEHIND'))
    ahead = corridor.add_train(service('AHEAD', origin=1))
    assert corridor.network.lengths(behind.route)[1] == 500000
    assert behind.launched and ahead.launched
    assert 0 < behind.authority < 10000 < 500000
    assert 0 < ahead.authority < 10000
    assert not ahead.blockers
    corridor.advance(1)
    assert behind.speed > 0 and ahead.speed > 0
    assert_exclusive(corridor)


def test_following_trains_share_original_edge_with_separate_logical_blocks(corridor):
    trains = []
    for tid, position in [('LEAD', 15000), ('MIDDLE', 8000), ('FOLLOW', 0)]:
        train = corridor.add_train(service(tid, destination=1), replan=False)
        # Seed existing consists at known, non-overlapping positions with no
        # untravelled authority; subsequent grants come from the dispatcher.
        train.x = position
        train.launched = True
        train.authority = position
        train.state = 'waiting'
        trains.append(train)
    corridor.replan('existing separated consists')
    assert all(t.authority > t.x for t in trains)
    footprints = [dispatch.resources(corridor, t, physical=True) for t in trains]
    assert all(any(r.startswith('b:0:') for r in footprint) for footprint in footprints)
    assert_exclusive(corridor)
    corridor.advance(5)
    assert all(t.speed > 0 for t in trains)
    assert_exclusive(corridor)


def test_useful_partial_grant_keeps_the_following_route(corridor, monkeypatch):
    leader = corridor.add_train(service('LEAD'), replan=False)
    leader.x = 4000
    leader.launched = True
    leader.authority = leader.x
    corridor.replan('leader already on the corridor')
    reroutes = []
    monkeypatch.setattr(corridor, 'try_reroute', lambda *args: reroutes.append(args) or False)
    follower = corridor.add_train(service('FOLLOW'))
    assert 0 < follower.authority < leader.x
    assert follower.route == leader.route
    assert not reroutes
    assert_exclusive(corridor)


def test_opposing_train_cannot_enter_free_far_end_of_committed_track(corridor):
    forward = corridor.add_train(service('FORWARD', destination=1))
    reverse = corridor.add_train(service('REVERSE', origin=1, destination=0))
    entry = route_blocks(corridor, reverse)[0]['resource']
    assert entry not in dispatch.resources(corridor, forward)
    assert not reverse.launched and reverse.authority is None
    assert reverse.blockers == [forward.spec.id]
    assert 'Opposing' in reverse.reason
    assert_exclusive(corridor)


def test_rear_releases_logical_block_and_incident_retains_extended_grant(corridor):
    train = corridor.add_train(service('FOLLOWING'))
    first = route_blocks(corridor, train)[0]
    initial_authority = train.authority
    train.x = first['exit'] + train.spec.length_m + corridor.config.clearance_m
    corridor.replan('rear exactly at boundary')
    assert first['resource'] in dispatch.resources(corridor, train, physical=True)
    assert train.authority > initial_authority
    train.x += .01
    corridor.replan('rear cleared boundary')
    assert first['resource'] not in dispatch.resources(corridor, train)
    corridor.advance(30)
    authority = train.authority
    previous_speed = train.speed
    corridor.add_incidents([IncidentSpec(id='BREAK', kind='train_breakdown',
        asset_type='train', asset_id=train.spec.id, duration_s=None)])
    corridor.advance(.2)
    assert previous_speed - train.speed <= train.spec.braking_mps2 * .2 + 1e-8
    corridor.advance(60)
    assert train.authority == authority and train.speed == 0
    assert_exclusive(corridor)
    corridor.clear_incident('BREAK')
    corridor.advance(5)
    assert train.speed > 0 and train.authority >= authority
    assert_exclusive(corridor)


def test_low_braking_rate_expands_grant_beyond_nominal_lookahead(corridor):
    train = corridor.add_train(service('HEAVY', max_speed_kmh=160, braking_mps2=.1))
    speed = train.spec.max_speed_kmh / 3.6
    braking_distance = speed ** 2 / (2 * train.spec.braking_mps2)
    protected = braking_distance + speed * 1.2 + corridor.config.clearance_m
    assert train.authority >= protected > corridor.config.authority_lookahead_m
    assert train.authority < protected + corridor.config.signal_block_m
    assert train.authority + corridor.config.clearance_m in [
        block['exit'] for block in route_blocks(corridor, train)]
    train.speed = speed
    corridor.advance(5)
    assert train.speed == pytest.approx(speed)
    assert_exclusive(corridor)
