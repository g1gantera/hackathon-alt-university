import math

import pytest

from backend.app.railway_map import locate, infrastructure
from backend.app.simulator import Simulator


def test_position_follows_bent_rail_and_reverses_bearing():
    topology = {'length_m':200, 'stations':[
        {'id':'A','position_m':0}, {'id':'B','position_m':200}],
        'sections':[{'id':'AB','from_station':'A','to_station':'B',
                     'length_m':200,'geometry':[[0,0],[0,.001],[.001,.001]]}]}
    north = locate(topology,50)
    assert north['coordinate'] == pytest.approx([0,.0005],abs=1e-9)
    assert north['bearing_deg'] == pytest.approx(0)
    assert locate(topology,50,-1)['bearing_deg'] == pytest.approx(180)
    east = locate(topology,150)
    assert east['coordinate'] == pytest.approx([.0005,.001],abs=1e-9)
    assert east['bearing_deg'] == pytest.approx(90,abs=.001)
    assert locate(topology,-20)['coordinate'] == [0,0]
    assert locate(topology,300)['coordinate'] == [.001,.001]
    with pytest.raises(ValueError):
        locate(topology,math.nan)


def test_map_and_live_train_coordinates_share_the_same_geometry():
    sim = Simulator()
    topology = sim.state['topology']
    features = infrastructure(topology)['features']
    assert sum(f['geometry']['type']=='Point' for f in features)==6
    assert sum(f['geometry']['type']=='LineString' for f in features)==5
    initial = sim.snapshot()
    assert initial['trains'][0]['destination_id']==topology['stations'][-1]['id']
    assert initial['trains'][1]['destination_id']==topology['stations'][0]['id']
    sim.state['running']=True
    sim.tick(60)
    changed=sim.snapshot()
    assert sum(t['status']=='moving' for t in changed['trains'])>=2
    for before,after in zip(initial['trains'],changed['trains']):
        assert len(after['route_station_ids'])==6
        assert after['position_source']=='simulation'
        assert after['coordinate']==locate(topology,after['position_m'],after['direction'])['coordinate']
        if after['status']=='moving':
            assert after['coordinate']!=before['coordinate']
