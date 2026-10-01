"""Signal indications and displayed occupation use canonical logical blocks."""
import pytest

from backend.engine import Engine
from backend.models import IncidentSpec, TrainSpec
from backend.network import Network


def corridor(lengths=(10000,)):
    """A straight mapped corridor, with no surveyed signal metadata."""
    net=Network.__new__(Network)
    net.vertices=[[70+i*.1,45] for i in range(len(lengths)+1)]
    net.edges=[{'id':i,'u':i,'v':i+1,'way':i,'length_m':length,
        'category':'rail','geometry':[net.vertices[i],net.vertices[i+1]]}
        for i,length in enumerate(lengths)]
    net.ways=[{'tags':{'maxspeed':'160'}} for _ in lengths]
    net.adj=[[] for _ in net.vertices]
    for edge in net.edges:
        net.adj[edge['u']].append(edge['id'])
        net.adj[edge['v']].append(edge['id'])
    net.allowed=set(range(len(lengths)))
    net.arc_lengths={}
    net.station_at={}
    net.find_demo=lambda:{}
    engine=Engine(net)
    train=engine.add_train(TrainSpec(id='FORWARD',name='Forward',origin=0,
        destination=len(lengths),length_m=10))
    return engine,train


def test_green_requires_two_clear_blocks_and_last_clear_block_is_yellow():
    engine,train=corridor()
    occupied=engine.signals['SIG-0-0']
    approach=engine.signals['SIG-0-0-1']
    last=engine.signals['SIG-0-0-2']
    assert occupied['owner']==occupied['occupied_by']==train.spec.id
    assert occupied['aspect']=='red'
    assert approach['aspect']=='green' and approach['clear_blocks_ahead']==2
    assert last['aspect']=='yellow' and last['clear_blocks_ahead']==1
    assert engine.signals['SIG-0-0-3']['aspect']=='red'


def test_short_graph_sections_cannot_give_green_without_braking_distance():
    engine,train=corridor((100,100,100,100))
    train.spec.max_speed_kmh=160
    train.spec.braking_mps2=.1
    engine.update_signals()
    signal=engine.signals['SIG-1-0']
    assert signal['clear_blocks_ahead']==3
    assert signal['available_distance_m']==300
    assert signal['aspect']=='yellow'


def test_opposite_signal_never_borrows_forward_owners_authority():
    engine,train=corridor()
    opposing=engine.add_train(TrainSpec(id='REVERSE',name='Reverse',
        origin=1,destination=0,length_m=10))
    assert not opposing.launched
    forward=engine.signals['SIG-0-0-1']
    reverse=engine.signals['SIG-0-1-1']
    assert forward['resource']==reverse['resource']=='b:0:1'
    assert forward['owner']==reverse['owner']==train.spec.id
    assert forward['aspect']=='green' and reverse['aspect']=='red'
    assert all(s['aspect']=='red' for s in engine.signals.values() if s['direction']==1)


@pytest.mark.parametrize('signal',['SIG-0-0-5','SIG-0-1-5','SIG-0-0-0',
    'SIG-0-1-4','SIG-0-0-x','SIG-0-2-1','SIG-99-0-1'])
def test_unknown_internal_signals_are_rejected(signal):
    engine,_=corridor()
    with pytest.raises(ValueError):
        engine.validate_incident(IncidentSpec(kind='signal_failure',
            asset_type='signal',asset_id=signal))


def test_internal_signal_failure_closes_parent_edge_and_retains_grant():
    engine,train=corridor()
    authority=train.authority
    engine.add_incidents([IncidentSpec(id='FAILED',kind='signal_failure',
        asset_type='signal',asset_id='SIG-0-0-2',duration_s=None)])
    assert train.authority==authority
    assert engine.closed_edges()=={0}
    assert all(s['failed'] and s['aspect']=='red' for s in engine.signals.values())


def test_snapshot_distinguishes_occupied_and_reserved_parts_of_same_edge():
    engine,_=corridor()
    state=engine.snapshot()
    train=state['trains'][0]
    assert train['occupied_edges']==train['reserved_edges']==[0]
    assert len(train['occupied_blocks'])==1
    assert len(train['reserved_blocks'])==3
    assert train['occupied_blocks'][0]['resource']=='b:0:0'
    assert all(b['occupied'] for b in train['occupied_blocks'])
    assert [b['occupied'] for b in train['reserved_blocks']]==[True,False,False]
    for block in train['reserved_blocks']:
        assert block['geometry'][0]==engine.network.position((0,0),block['start_m'])
        assert block['geometry'][-1]==engine.network.position((0,0),block['end_m'])
    assert state['dispatcher']['assumed_signals'] is True
    assert state['dispatcher']['signal_failure_scope']=='Entire parent map edge'
