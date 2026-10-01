"""Assumed signal blocks over the immutable mapped track geometry.

The source graph does not contain a verified signalling inventory. These
uniform subdivisions are simulation boundaries, not added tracks or surveyed
signals. Resource IDs use canonical edge offsets so both directions lock the
same physical block.
"""
import math


def block_count(engine, eid):
    """Number of assumed signal blocks on an original mapped edge."""
    if engine.config.signal_block_m <= 0:
        raise ValueError('Signal block length must be positive')
    return max(1, math.ceil(engine.network.edges[eid]['length_m'] / engine.config.signal_block_m))


def route_blocks(engine, train):
    """Return ordered block dictionaries; callers must not mutate the layout.

    Entry and exit are distances along this train's route. ``start_m`` and
    ``end_m`` are increasing offsets in the original edge's u-to-v direction.
    A short edge retains its existing ``e:<id>`` resource and signal ID.
    """
    route = tuple(train.route)
    block_size = engine.config.signal_block_m
    cached = getattr(train, '_block_layout_cache', None)
    if cached is not None and cached[:2] == (route, block_size):
        return cached[2]
    if block_size <= 0:
        raise ValueError('Signal block length must be positive')

    result = []
    distance = 0.0
    for arc in route:
        eid, direction = arc
        length = engine.network.edges[eid]['length_m']
        count = block_count(engine, eid)
        width = length / count
        indices = range(count) if direction == 0 else range(count - 1, -1, -1)
        for order, index in enumerate(indices):
            start = index * width
            end = length if index == count - 1 else (index + 1) * width
            result.append({
                'resource': f'e:{eid}' if count == 1 else f'b:{eid}:{index}',
                'edge': eid,
                'direction': direction,
                'index': index,
                'entry': distance + (start if direction == 0 else length - end),
                'exit': distance + (end if direction == 0 else length - start),
                'start_m': start,
                'end_m': end,
                'vertex': engine.network.startpoint(arc) if order == 0 else None,
                'signal_id': f'SIG-{eid}-{direction}' + (f'-{index}' if order else ''),
                # Static signal location, computed once per route layout.
                'position': engine.network.position((eid, 0), start if direction == 0 else end),
            })
        distance += length
    train._block_layout_cache = (route, block_size, result)
    return result


def resource_edge(resource):
    """Decode a whole-edge or subdivided-block resource; junctions return None."""
    parts = resource.split(':')
    if (len(parts) == 2 and parts[0] == 'e' and parts[1].isdigit()
            or len(parts) == 3 and parts[0] == 'b'
            and parts[1].isdigit() and parts[2].isdigit()):
        return int(parts[1])
    return None


def block_edges(resources):
    """Aggregate logical block resources into original map edge IDs."""
    return {eid for resource in resources if (eid := resource_edge(resource)) is not None}


def block_geometry(network, block):
    """Return the block's canonical polyline as Leaflet [latitude, longitude]."""
    eid = block['edge']
    edge = network.edges[eid]
    start = block['start_m']
    end = block['end_m']
    first = network.position((eid, 0), start)
    last = network.position((eid, 0), end)
    # position() caches measured polyline lengths, which may differ from the
    # source's length_m; apply the same proportional mapping to interior bends.
    measured = network.arc_lengths[eid]
    lower = start / edge['length_m'] * measured[-1]
    upper = end / edge['length_m'] * measured[-1]
    interior = [[point[1], point[0]]
                for offset, point in zip(measured[1:-1], edge['geometry'][1:-1])
                if lower < offset < upper]
    return [first, *interior, last]


def resource_label(resource):
    """Name a simulator resource without presenting it as surveyed equipment."""
    eid = resource_edge(resource)
    if eid is not None:
        if resource.startswith('b:'):
            return f'block E{eid}/{int(resource.split(":")[2]) + 1}'
        return f'block E{eid}'
    if resource.startswith('v:') and resource[2:].isdigit():
        return f'junction V{resource[2:]}'
    return resource
