from backend import dispatch
from backend.models import TrainSpec


def test_wait_for_cycle_is_reported_without_breaking_locks(engine):
    """Two stopped consists face each other on one single-track edge, no loop between."""
    a,b=engine.demo['origin'],engine.demo['destination']
    left=engine.add_train(TrainSpec(id='LEFT',name='Left',origin=a,destination=b),replan=False)
    right=engine.add_train(TrainSpec(id='RIGHT',name='Right',origin=b,destination=a),replan=False)
    middle=engine.network.edges[left.route[0][0]]['length_m']/2
    left.x=middle-1000
    right.x=right.total-(middle+1000)
    for t in (left,right):
        t.launched=True
        t.state='waiting'
    footprints={t.spec.id:dispatch.resources(engine,t,physical=True) for t in (left,right)}
    engine.replan('existing stranded consists')
    assert set(engine.deadlock)=={'LEFT','RIGHT'}
    assert not engine.invariant_errors()
    assert all(t.authority is None for t in (left,right))
    assert footprints=={t.spec.id:dispatch.resources(engine,t,physical=True) for t in (left,right)}
    engine.advance(2)
    assert engine.quality.result(engine)['factors']['conflicts']['value']==0


def test_consists_either_side_of_a_loop_meet_instead_of_deadlocking(engine):
    """With a passing loop between them, opposing stranded consists can meet."""
    a,b=engine.demo['origin'],engine.demo['destination']
    left=engine.add_train(TrainSpec(id='LEFT',name='Left',origin=a,destination=b),replan=False)
    right=engine.add_train(TrainSpec(id='RIGHT',name='Right',origin=b,destination=a),replan=False)
    for t in (left,right):
        t.launched=True
        t.x=engine.network.edges[t.route[0][0]]['length_m']/2
        t.state='waiting'
    engine.replan('stranded consists either side of a loop')
    for _ in range(600):
        engine.advance(5)
        assert not engine.invariant_errors() and not engine.deadlock
        if left.state==right.state=='completed':
            break
    assert left.state==right.state=='completed'
