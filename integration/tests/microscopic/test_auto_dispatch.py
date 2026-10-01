"""Automatic meets, overtakes and section order without scripted sidings."""
import json

from backend.app.microscopic import dispatch, meets
from backend.app.microscopic.engine import Engine
from backend.app.microscopic.history import History
from backend.app.microscopic.models import Settings, TrainSpec


def spec(engine, tid, forward=True, **changes):
    a, b = engine.demo['origin'], engine.demo['destination']
    return TrainSpec(**{'id': tid, 'name': tid, 'origin': a if forward else b,
        'destination': b if forward else a, **changes})


def off_main(engine, t):
    """Edges of an existing loop track this train used instead of the main line."""
    return {eid for eid, _ in t.route} - {eid for eid, _ in engine.demo['bypass_route']}


def assert_direction_exclusive(engine):
    """No two opposing direction commitments may ever overlap."""
    active = [t for t in engine.trains.values() if t.launched and t.state != 'completed']
    held = {t.spec.id: dispatch.movements(engine, t, meets.committed_limit(t) if t.authority is not None else t.x) for t in active}
    for t in active:
        assert not dispatch.incompatible_routes(t, held[t.spec.id], held), t.spec.id


def run(engine, seconds):
    for _ in range(int(seconds/5)):
        engine.advance(5)
        assert not engine.invariant_errors()
        assert not engine.deadlock
        assert_direction_exclusive(engine)
        if all(t.state == 'completed' for t in engine.trains.values()):
            return


def late(t):
    return t.arrivals[-1]['time']-t.spec.scheduled_arrival_s


def test_opposing_trains_meet_at_existing_loop_without_script(engine):
    local = engine.add_train(spec(engine, 'LOCAL', importance=3, max_speed_kmh=70, scheduled_arrival_s=1800), replan=False)
    express = engine.add_train(spec(engine, 'EXP', False, importance=9, max_speed_kmh=100, scheduled_arrival_s=1400))
    run(engine, 3000)
    assert local.state == express.state == 'completed'
    # One train took an existing loop track; neither waited for the other's whole trip.
    assert off_main(engine, local) or off_main(engine, express)
    assert local.actual_departure < 60 and express.actual_departure < 60
    assert late(local) < 180 and late(express) < 180
    assert any(d['conflict'] == 'meet' for d in engine.decisions)


def test_whole_leg_locking_when_automatic_dispatch_is_off(network):
    engine = Engine(network, History(':memory:'), Settings(auto_dispatch=False))
    local = engine.add_train(spec(engine, 'LOCAL', importance=3, max_speed_kmh=70, scheduled_arrival_s=1800), replan=False)
    express = engine.add_train(spec(engine, 'EXP', False, importance=9, max_speed_kmh=100, scheduled_arrival_s=1400))
    run(engine, 3200)
    # Without passing-point sections one train waits for the other's whole leg.
    assert max(local.actual_departure, express.actual_departure) > 1000
    assert not engine.decisions


def test_fast_follower_overtakes_slow_leader_in_loop(engine):
    local = engine.add_train(spec(engine, 'LOCAL', importance=3, max_speed_kmh=60, scheduled_arrival_s=1800), replan=False)
    express = engine.add_train(spec(engine, 'EXP', importance=9, departure_s=60, max_speed_kmh=120, scheduled_arrival_s=1000))
    run(engine, 3000)
    assert local.state == express.state == 'completed'
    assert express.arrivals[-1]['time'] < local.arrivals[-1]['time']
    assert off_main(engine, local) and not off_main(engine, express)
    overtakes = [d for d in engine.decisions if d['conflict'] == 'overtake']
    assert overtakes and overtakes[0]['kind'] == 'overtake'
    # The record explains the alternatives that were compared.
    assert {o['kind'] for o in overtakes[0]['options']} >= {'overtake', 'follow'}


def test_low_priority_train_yields_section_to_imminent_express_without_loop(engine, monkeypatch):
    # Remove passing points: only the order of entering the single track remains.
    monkeypatch.setattr(meets, 'find_loops', lambda engine, t: [])
    local = engine.add_train(spec(engine, 'LOCAL', importance=2, max_speed_kmh=70, scheduled_arrival_s=1800), replan=False)
    express = engine.add_train(spec(engine, 'EXP', False, importance=10, departure_s=60, max_speed_kmh=100, scheduled_arrival_s=1300))
    engine.replan('both staged')
    assert not local.launched and 'Gives way' in local.reason
    run(engine, 3600)
    assert express.arrivals[-1]['time'] < local.actual_departure+60
    assert late(express) < 120


def test_snapshot_is_strict_json_with_open_sections(engine):
    engine.add_train(spec(engine, 'LOCAL', scheduled_arrival_s=1800), replan=False)
    engine.add_train(spec(engine, 'EXP', False, importance=9, scheduled_arrival_s=1400))
    engine.advance(30)
    state = engine.snapshot()
    json.dumps(state, allow_nan=False)
    assert all('section_end_m' in t and 'reserved_tracks' in t for t in state['trains'])
