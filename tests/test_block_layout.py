"""Logical signalling overlays keep map topology and direction identity intact."""
from types import SimpleNamespace

import pytest

from backend.blocks import block_edges, block_geometry, resource_edge, route_blocks
from backend.network import Network


def layout(length=5000, direction=0):
    net = Network.__new__(Network)
    net.edges = [{'id': 0, 'u': 0, 'v': 1, 'length_m': length,
                  'geometry': [[70, 45], [70.02, 45.01], [70.04, 45]]}]
    net.arc_lengths = {}
    engine = SimpleNamespace(network=net, config=SimpleNamespace(signal_block_m=2000))
    train = SimpleNamespace(route=[(0, direction)])
    return engine, train


def test_subdivision_preserves_canonical_resources_in_both_directions():
    engine, forward = layout()
    reverse = SimpleNamespace(route=[(0, 1)])
    blocks = route_blocks(engine, forward)
    opposite = route_blocks(engine, reverse)
    assert len(blocks) == 3
    assert [b['resource'] for b in blocks] == [b['resource'] for b in reversed(opposite)]
    assert blocks[0]['entry'] == opposite[0]['entry'] == 0
    assert blocks[-1]['exit'] == opposite[-1]['exit'] == 5000
    for rows in (blocks, opposite):
        assert all(0 < b['exit'] - b['entry'] <= 2000 for b in rows)
        assert all(a['exit'] == pytest.approx(b['entry']) for a, b in zip(rows, rows[1:]))
        assert all(b['vertex'] is None for b in rows[1:])
    assert blocks[0]['vertex'] == 0 and opposite[0]['vertex'] == 1
    assert blocks[0]['signal_id'] == 'SIG-0-0'
    assert opposite[0]['signal_id'] == 'SIG-0-1'
    assert opposite[1]['signal_id'] == 'SIG-0-1-1'


def test_short_edges_keep_existing_ids_and_cache_tracks_route_and_setting():
    engine, train = layout(1500)
    initial = route_blocks(engine, train)
    assert initial[0]['resource'] == 'e:0'
    assert route_blocks(engine, train) is initial
    engine.config.signal_block_m = 1000
    split = route_blocks(engine, train)
    assert split is not initial and len(split) == 2
    train.route = [(0, 1)]
    reverse = route_blocks(engine, train)
    assert reverse is not split and reverse[0]['resource'] == 'b:0:1'


def test_resource_aggregation_ignores_junctions_and_invalid_ids():
    resources = ['e:7', 'b:3:0', 'b:3:1', 'v:2', 'b:bad:0', 'b:1']
    assert block_edges(resources) == {3, 7}
    assert resource_edge('e:7') == 7
    assert resource_edge('b:3:1') == 3
    assert resource_edge('v:2') is None


def test_block_geometry_clips_endpoints_and_preserves_internal_bends():
    engine, train = layout()
    middle = route_blocks(engine, train)[1]
    geometry = block_geometry(engine.network, middle)
    assert geometry[0] == engine.network.position((0, 0), middle['start_m'])
    assert geometry[-1] == engine.network.position((0, 0), middle['end_m'])
    assert [45.01, 70.02] in geometry
    assert [45, 70] not in geometry and [45, 70.04] not in geometry
