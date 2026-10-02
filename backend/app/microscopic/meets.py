"""Section-by-section dispatching through passing points, and overtakes.

A passing point is an existing loop: a parallel path that leaves a train's
route at junction u and rejoins it at v. Following the single-track rules
(PTE 142, 144), a train facing opposing traffic commits the line one section
at a time, from passing point to passing point, and may enter a section only
when a free receiving track that fits the consist is reserved at its end.
When opposing traffic of equal or higher priority is expected, the train takes
the loop track and leaves the main line for the other train. Meets therefore
emerge for any number of trains instead of being scripted, and no train locks
a whole corridor while others could pass it.

Without opposing traffic a train runs through passing points unreserved, so
following movements keep the ordinary block headway.

Overtakes are planned pairwise: when a faster or higher-priority follower
would lose more priority-weighted lateness than the leader loses by waiting,
the leader is routed into a loop and held until the follower has passed.

Holds and section limits only cap future commitments. They never revoke a
grant, and exclusive block and junction locking in ``dispatch`` still
validates every movement. Timings are free-running estimates.
"""
from .blocks import resource_edge

INF = float('inf')
LOOP_MAX_M = 5000
LOOP_MAX_EDGES = 4
# Commit the next section when the train is this far beyond its braking horizon.
SECTION_MARGIN_M = 2000
# An opposing train meets us at a passing point if it arrives there before
# this long after our rear has cleared it; later trains are met farther on.
MEET_WINDOW_S = 120
# Yield a section only when clearly cheaper, so two trains never both yield.
YIELD_RATIO = .8
HORIZON_S = 3600
# Raw time lost counts a little even inside timetable slack: holds still cost
# energy, crew time and line capacity.
SLACK_FACTOR = .2
MIN_CONFLICT_S = 30
# Trains reaching a merge within this margin are ordered by dispatch priority.
MERGE_MARGIN_S = 60
SCAN_INTERVAL_S = 5
DECISION_LOG = 20


def commit_limit(t):
    """Distance up to which a train may commit: stop, section end or hold."""
    limit = t.next_stop['distance']
    if t.section_end is not None:
        limit = min(limit, t.section_end)
    if t.hold is not None:
        limit = min(limit, t.hold['distance'])
    return limit


def committed_limit(t):
    """Direction commitment in force: validated at a grant, shrunk by limits."""
    limit = commit_limit(t)
    return limit if t.commit_to is None else min(limit, t.commit_to)


def horizon_m(engine, t):
    """Requested authority: configured lookahead or the braking envelope."""
    speed = t.spec.max_speed_kmh/3.6
    return max(engine.config.authority_lookahead_m,
        speed**2/(2*t.spec.braking_mps2)+speed*1.2+engine.config.clearance_m)


def _route_cache(t, name, build):
    route = tuple(t.route)
    cached = getattr(t, name, None)
    if cached is not None and cached[0] == route:
        return cached[1]
    value = build(route)
    setattr(t, name, (route, value))
    return value


def vertex_distances(engine, t):
    """Every route distance at which each vertex is passed."""
    net = engine.network

    def build(route):
        lengths = net.lengths(t.route)
        result = {}
        for k, arc in enumerate(route):
            result.setdefault(net.startpoint(arc), []).append(lengths[k])
        result.setdefault(net.endpoint(route[-1]), []).append(lengths[-1])
        return result
    return _route_cache(t, '_vertex_cache', build)


def remaining_vertex(engine, t, vertex):
    """First distance of ``vertex`` not yet cleared by the rear, or None."""
    rear = t.x-t.spec.length_m-engine.config.clearance_m
    return next((d for d in vertex_distances(engine, t).get(vertex, []) if d >= rear-1e-6), None)


def find_loops(engine, t):
    """Existing parallel paths that leave and rejoin this train's route.

    A loop starts at route vertex u, follows at most ``LOOP_MAX_EDGES`` edges
    that are not on the route and rejoins it at a later vertex v. Every turn
    obeys the same heading and one-way rules as ordinary routing. No track is
    inferred; a loop is only used if the consist fits clear of both junctions.
    """
    net = engine.network

    def build(route):
        lengths = net.lengths(t.route)
        index = {}
        for k, arc in enumerate(route):
            index.setdefault(net.startpoint(arc), k)
        index.setdefault(net.endpoint(route[-1]), len(route))
        used = {eid for eid, _ in route}
        loops = []

        def extend(a, path, length, at):
            incoming = path[-1] if path else (route[a-1] if a else None)
            for eid in net.adj[at]:
                if eid in used or eid not in net.allowed or any(p[0] == eid for p in path):
                    continue
                edge = net.edges[eid]
                arc = (eid, 0 if edge['u'] == at else 1)
                if not net.direction_allowed(arc) or not net.compatible(incoming, arc):
                    continue
                total = length+edge['length_m']
                if total > LOOP_MAX_M:
                    continue
                w = net.endpoint(arc)
                b = index.get(w)
                if b is not None:
                    # A route vertex ends the search whether or not it is a valid exit.
                    if a < b < len(route) and lengths[b]-lengths[a] <= LOOP_MAX_M and net.compatible(arc, route[b]):
                        loops.append({'a': a, 'b': b, 'u': net.startpoint(route[a]), 'v': w,
                            'path': path+[arc], 'length': total, 'main_length': lengths[b]-lengths[a],
                            'name': '/'.join(f'E{e}' for e, _ in path+[arc])})
                    continue
                if len(path)+1 < LOOP_MAX_EDGES:
                    extend(a, path+[arc], total, w)

        for a, arc in enumerate(route):
            extend(a, [], 0.0, net.startpoint(arc))
        return loops
    return _route_cache(t, '_loop_cache', build)


def passing_points(engine, t):
    """Non-overlapping passing points along the route, in route order."""
    net = engine.network

    def build(route):
        ranges = {}
        for loop in find_loops(engine, t):
            ranges.setdefault((loop['a'], loop['b']), []).append(loop)
        points, end = [], -1
        # Earliest-ending first keeps as many independent passing points as possible.
        for a, b in sorted(ranges, key=lambda r: (r[1], r[0])):
            if a >= end:
                points.append({'a': a, 'b': b, 'u': net.startpoint(route[a]), 'v': net.startpoint(route[b]),
                    'alts': sorted(ranges[(a, b)], key=lambda loop: (loop['length'], loop['name']))})
                end = b
        return points
    return _route_cache(t, '_pp_cache', build)


def edge_speed(engine, t, eid):
    return max(1, min(t.spec.max_speed_kmh, engine.network.speed_limit(eid), engine.restriction(eid))/3.6)


def restart_s(t):
    return min(t.spec.max_speed_kmh/3.6, 20)/t.spec.acceleration_mps2/2


class Timeline:
    """Free-running passing times along a train's current route."""

    def __init__(self, engine, t):
        net = engine.network
        self.lengths = lengths = net.lengths(t.route)
        self.speeds = [edge_speed(engine, t, eid) for eid, _ in t.route]
        clock = max(engine.sim_time, t.spec.departure_s, t.dwell_until)
        if t.speed < 1:
            clock += restart_s(t)
        self.index = net.locate(t.route, t.x)[0]
        self.x = t.x
        self.start = clock
        self.stops = sorted((s['distance'], s['dwell']) for s in t.stop_points[t.stop_index:-1] if s['distance'] > t.x)
        self.times = [clock]*(len(t.route)+1)
        position, pointer = t.x, 0
        for k in range(self.index, len(t.route)):
            end = lengths[k+1]
            clock += max(0, end-position)/self.speeds[k]
            while pointer < len(self.stops) and self.stops[pointer][0] <= end+1e-6:
                clock += self.stops[pointer][1]
                pointer += 1
            self.times[k+1] = clock
            position = end
        self.arrival = clock

    def edge(self, distance):
        """Index of the route edge containing ``distance``."""
        lengths = self.lengths
        k = self.index
        while k < len(lengths)-2 and lengths[k+1] <= distance:
            k += 1
        return k

    def at(self, distance):
        """Estimated time the head reaches ``distance`` along the route."""
        if distance <= self.x:
            return self.start
        if distance >= self.lengths[-1]:
            return self.arrival+(distance-self.lengths[-1])/self.speeds[-1]
        k = self.edge(distance)
        base = max(self.x, self.lengths[k])
        dwell = sum(d for at, d in self.stops if base+1e-6 < at <= distance)
        return (self.times[k] if self.lengths[k] >= self.x else self.start)+(distance-base)/self.speeds[k]+dwell


def timeline(engine, t):
    """Free-running timeline, cached until the train's route or position changes."""
    cache = engine.__dict__.setdefault('_timelines', {})
    key = (tuple(t.route), t.x, t.speed, t.stop_index, engine.sim_time)
    found = cache.get(t.spec.id)
    if found is None or found[0] != key:
        found = (key, Timeline(engine, t))
        cache[t.spec.id] = found
    return found[1]


def weight(engine, t):
    """Priority weight: a second of an importance-10 train costs most."""
    return 1+engine.config.weights.importance*(t.spec.importance-1)/9


def delay_cost(engine, t, line, delay):
    """Priority-weighted lateness added by ``delay`` seconds, plus a slack term."""
    if delay <= 0:
        return 0.0
    target = t.spec.scheduled_arrival_s
    late = delay if target is None else max(0, line.arrival-target+delay)-max(0, line.arrival-target)
    return weight(engine, t)*(late+SLACK_FACTOR*delay)


def stops_inside(t, start, end):
    return any(start+1e-6 < s['distance'] < end-1e-6 for s in t.stop_points[t.stop_index:])


def log_decision(engine, record):
    engine.log('conflict', f'{record["conflict"].capitalize()} {"/".join(record["trains"])}: {record["chosen"]}',
        record['trains'], after=record)
    engine.decisions.append(record)
    del engine.decisions[:-DECISION_LOG]


# Passing-point sections ------------------------------------------------------

def tracks_at(engine, t, p):
    """The main line and loop tracks of passing point ``p`` for train ``t``."""
    lengths = engine.network.lengths(t.route)
    main = {'edges': frozenset(e for e, _ in t.route[p['a']:p['b']]), 'arcs': t.route[p['a']:p['b']],
        'length': lengths[p['b']]-lengths[p['a']], 'loop': None, 'name': 'main line'}
    alts = [{'edges': frozenset(e for e, _ in loop['path']), 'arcs': loop['path'], 'length': loop['length'],
        'loop': loop, 'name': f'loop {loop["name"]}'} for loop in p['alts']]
    return main, alts


def track_free(engine, t, track):
    """No other train reserves the track, or claims or commits it against us.

    A same-direction train only passing through ahead does not prevent a
    following train from planning to hold there: block signalling separates
    them and the track is clear by the time the follower arrives.
    """
    me = t.spec.id
    if track['edges'] & engine.closed_edges():
        return False
    for eid, direction in track.get('arcs') or [(e, None) for e in track['edges']]:
        if engine.tracks.get(eid, me) != me:
            return False
        for owner in (engine._claimed_edges.get(eid, set()) | engine._committed_edges.get(eid, set()))-{me}:
            if direction is None or (eid, direction) not in engine._route_arcs.get(owner, ()):
                return False
    return True


def expected_opposing(engine, t, p, window=MEET_WINDOW_S):
    """Opposing trains due at this passing point while ``t`` uses it."""
    line = timeline(engine, t)
    ours = line.at(line.lengths[p['b']]+t.spec.length_m+engine.config.clearance_m)
    result = []
    for o in engine.trains.values():
        if o is t or o.state in {'arrived', 'completed'}:
            continue
        du = remaining_vertex(engine, o, p['u'])
        if du is None:
            continue
        # It traverses v before u; a train already between them is inside.
        entries = [d for d in vertex_distances(engine, o).get(p['v'], []) if d < du]
        if entries and timeline(engine, o).at(max(entries)) <= ours+window:
            result.append(o)
    return result


def leaves_room(engine, t, p, choice, tracks, opposing):
    """Never fill a passing point against expected opposing traffic.

    Unless an opposing train already holds a track here, at least one other
    free track must still fit an expected opposing consist. Otherwise trains
    of one direction could occupy every track and deadlock the section.
    """
    c = engine.config.clearance_m
    # Passing points are found per route, so two trains may delimit the same
    # loop area by different junctions; shared track edges identify it.
    here = {e for tr in tracks for e in tr['edges']}
    if any(here & set(r['edges']) for o in opposing for r in o.tracks):
        return True
    need = min(o.spec.length_m for o in opposing)+2*c+1
    return any(tr is not choice and tr['length'] >= need and not (tr['edges'] & choice['edges'])
        and track_free(engine, t, tr) for tr in tracks)


def can_pass(engine, t, o, base):
    """Whether opposing ``o`` can pass ``t`` waiting at ``base``."""
    if not t.launched:
        return True
    footprint = {resource_edge(r) for r in engine._footprints.get(t.spec.id, ())} - {None}
    if not footprint & {eid for eid, _ in o._ahead}:
        return True
    lengths = engine.network.lengths(t.route)
    c = engine.config.clearance_m
    for p in passing_points(engine, t):
        if lengths[p['a']]-1e-6 <= base <= lengths[p['b']]+1e-6:
            main, alts = tracks_at(engine, t, p)
            return any(not (tr['edges'] & footprint) and tr['length'] >= o.spec.length_m+2*c+1
                and track_free(engine, o, tr) for tr in (main, *alts))
    return False


def section_yield(engine, t, base, far, far_vertex):
    """The opposing train ``t`` should let through the section ahead, if any.

    Compares priority-weighted lateness of entering first (the opposing train
    waits at the far end) with waiting here until it has cleared the section.
    """
    if far_vertex is None:
        return None
    c = engine.config.clearance_m
    line = timeline(engine, t)
    lengths = line.lengths
    k = next((i for i in range(len(lengths)) if lengths[i] >= base-1e-6), len(lengths)-1)
    near_vertex = engine.network.startpoint(t.route[k]) if k < len(t.route) else engine.network.endpoint(t.route[-1])
    zone = {(eid, 1-d) for eid, d in t.route[k:line.edge(far-1)+1]}
    our_start = line.at(base)
    our_clear = line.at(far+t.spec.length_m+c)
    best = None
    for o in engine.trains.values():
        if o is t or o.state in {'arrived', 'completed'} or not hasattr(o, '_ahead') or not zone & o._ahead.keys():
            continue
        enter = remaining_vertex(engine, o, far_vertex)
        leave = remaining_vertex(engine, o, near_vertex)
        if enter is None or leave is None or leave <= enter:
            continue
        lo = timeline(engine, o)
        o_in = lo.at(enter)
        if o_in >= our_clear or o_in-engine.sim_time > HORIZON_S or not can_pass(engine, t, o, base):
            continue
        o_wait = our_clear-o_in
        go = delay_cost(engine, o, lo, o_wait+restart_s(o))
        wait = max(0, lo.at(leave+o.spec.length_m+c)-our_start)
        stay = delay_cost(engine, t, line, wait+(restart_s(t) if t.speed > 1 else 0))
        if stay < YIELD_RATIO*go and (best is None or o_in < best[1]):
            best = (o, o_in, go, stay, o_wait, wait)
    return best


def reroute_through(engine, t, loop):
    """Replace the main-line segment between u and v by the loop path."""
    start = engine.network.lengths(t.route)[loop['a']]
    delta = loop['length']-loop['main_length']
    t.route = t.route[:loop['a']]+loop['path']+t.route[loop['b']:]
    t.stop_points = [s if s['distance'] <= start+1e-6 else {**s, 'distance': s['distance']+delta} for s in t.stop_points]
    t.total = engine.network.lengths(t.route)[-1]


def reserve(engine, t, track, vertex, hold):
    t.tracks.append({'edges': sorted(track['edges']), 'exit': vertex, 'name': track['name'], 'hold_m': hold})
    for eid in track['edges']:
        engine.tracks[eid] = t.spec.id


def release_tracks(engine):
    """Drop reservations whose exit junction the rear has cleared."""
    engine.tracks = {}
    for t in engine.trains.values():
        if t.state == 'completed':
            t.tracks = []
        t.tracks = [r for r in t.tracks if remaining_vertex(engine, t, r['exit']) is not None]
        for r in t.tracks:
            for eid in r['edges']:
                engine.tracks[eid] = t.spec.id


def advance_section(engine, t, commitments):
    """Commit the next section up to a passing point with a free receiving track.

    Returns None, or (blockers, reason) when the train cannot commit further.
    """
    from . import dispatch
    if not engine.config.auto_dispatch:
        t.section_end = None
        t.tracks = []
        return None
    c = engine.config.clearance_m
    stop = t.next_stop['distance']
    position = max(t.x, t.authority if t.authority is not None else t.x)
    current = t.section_end
    if current is not None and (current >= stop-1e-6 or t.x+horizon_m(engine, t)+SECTION_MARGIN_M < current):
        return None
    base = position if current is None else current
    lengths = engine.network.lengths(t.route)
    target = None
    fallback = None
    for p in passing_points(engine, t):
        start, end = lengths[p['a']], lengths[p['b']]
        if start < base-1e-6:
            continue
        if end > stop+1e-6 or stops_inside(t, start, end):
            break
        opposing = expected_opposing(engine, t, p)
        if not opposing:
            # Nobody to meet here: run through, but remember it as a place to
            # wait if the meeting point farther on cannot receive us yet.
            main, alts = tracks_at(engine, t, p)
            spare = next((tr for tr in (main, *alts) if tr['length'] >= t.spec.length_m+2*c+1 and track_free(engine, t, tr)), None)
            if spare is not None:
                fallback = (p, spare, None, False)
            continue
        main, alts = tracks_at(engine, t, p)
        heaviest = max(opposing, key=lambda o: (weight(engine, o), o.spec.id))
        give_way = weight(engine, heaviest) >= weight(engine, t)
        order = [*alts, main] if give_way else [main, *alts]
        fits = [tr for tr in order if tr['length'] >= t.spec.length_m+2*c+1]
        if not fits:
            # Too short to hold this consist clear of both junctions: run through.
            continue
        choice = next((tr for tr in fits if track_free(engine, t, tr)), None)
        if choice is not None and not leaves_room(engine, t, p, choice, [main, *alts], opposing):
            choice = None
        if choice is None and fallback is not None:
            # Advance to the last free passing point and wait there instead.
            target = fallback
            break
        if choice is None:
            if current is None:
                t.section_end = position
            owners = sorted({o for tr in fits for e in tr['edges']
                for o in ({engine.tracks.get(e)} | engine._claimed_edges.get(e, set()) | engine._committed_edges.get(e, set()))}
                - {None, t.spec.id})
            return owners, f'No free receiving track at passing point {engine.network.label(p["v"])} (V{p["u"]}–V{p["v"]})'
        target = (p, choice, heaviest, give_way)
        break
    if target is not None:
        far, far_vertex = lengths[target[0]['a']], target[0]['u']
    else:
        far, far_vertex = stop, t.next_stop.get('vertex')
    yielded = section_yield(engine, t, base, far, far_vertex)
    if yielded is not None:
        o, _, go, stay, o_wait, wait = yielded
        if current is None:
            t.section_end = position
        note = ('yield', t.spec.id, o.spec.id)
        if note not in engine.noted_conflicts:
            engine.noted_conflicts.add(note)
            log_decision(engine, {'time_s': round(engine.sim_time, 1), 'conflict': 'meet', 'kind': 'order',
                'trains': sorted([t.spec.id, o.spec.id]), 'chosen': f'{t.spec.id} waits for {o.spec.id} to clear the single-track section',
                'options': [{'label': f'{o.spec.id} first; {t.spec.id} waits', 'kind': 'order', 'cost': round(stay, 1), 'delays_s': {t.spec.id: round(wait, 1)}},
                    {'label': f'{t.spec.id} first; {o.spec.id} waits at the far passing point', 'kind': 'order', 'cost': round(go, 1), 'delays_s': {o.spec.id: round(o_wait, 1)}}]})
        return [o.spec.id], f'Gives way: {o.spec.id} clears the single-track section first (priority-weighted lateness {stay:.0f} vs {go:.0f})'
    before = (t.route, t.stop_points, t.total)
    if target is not None:
        p, choice, heaviest, give_way = target
        start = lengths[p['a']]
        if choice['loop'] is not None:
            reroute_through(engine, t, choice['loop'])
        new_end = start+choice['length']-c
    else:
        new_end = INF
    intended = dispatch.movements(engine, t, min(t.next_stop['distance'], new_end))
    incompatible = dispatch.incompatible_routes(t, intended, commitments)
    if incompatible:
        t.route, t.stop_points, t.total = before
        if current is None:
            t.section_end = position
        owners = sorted(set(incompatible.values()))
        return owners, 'Opposing section committed by '+', '.join(owners)+'; waiting at the last passing point or signal'
    t.section_end = new_end
    if target is not None:
        reserve(engine, t, choice, p['v'], new_end)
        if choice['loop'] is not None:
            engine.log('reroute', f'{t.spec.id} takes {choice["name"]} at passing point V{p["u"]}–V{p["v"]}',
                [t.spec.id], before=[a[0] for a in before[0]], after=[a[0] for a in t.route])
        if heaviest is None:
            # A waiting place short of a meeting point that cannot receive us yet.
            return None
        log_decision(engine, {'time_s': round(engine.sim_time, 1), 'conflict': 'meet', 'kind': 'meet',
            'trains': sorted([t.spec.id, heaviest.spec.id]), 'options': [],
            'chosen': f'{t.spec.id} reserves {choice["name"]} at passing point V{p["u"]}–V{p["v"]} for opposing {heaviest.spec.id}; '
                + ('gives way and leaves the main line free' if choice['loop'] is not None and give_way
                   else 'loop track occupied, holds on the main line' if give_way
                   else 'keeps the main line; the lower-priority train takes the loop')})
    return None


# Overtakes -------------------------------------------------------------------

def index_trains(engine, trains):
    for t in trains:
        t._index = engine.network.locate(t.route, t.x)[0]
        t._ahead = {arc: k for k, arc in enumerate(t.route) if k >= t._index}


def shared_arcs(a, b):
    """(index in a, index in b) of same-direction arcs on both remaining routes."""
    ahead = b._ahead
    return [(k, ahead[a.route[k]]) for k in range(a._index, len(a.route)) if a.route[k] in ahead]


def committed_before(t, distance):
    """True when this train has not committed beyond ``distance``."""
    return t.authority is None or t.authority <= distance+1e-6


def option(label, kind, cost, delays, **plan):
    return {'label': label, 'kind': kind, 'cost': round(cost, 1),
        'delays_s': {k: round(v, 1) for k, v in delays.items()}, **plan}


def overtake_options(engine, lead, follow, tl, tf, span):
    """Options where ``lead`` waits in a loop while ``follow`` overtakes."""
    if lead.spec.via_siding is not None:
        return []
    c = engine.config.clearance_m
    shared = {k for k, _ in span}
    result = []
    for loop in find_loops(engine, lead):
        # The follower must run the loop's main line and continue past its exit.
        if not all(k in shared for k in range(loop['a'], loop['b']+1)):
            continue
        start, end = tl.lengths[loop['a']], tl.lengths[loop['b']]
        track = {'edges': frozenset(e for e, _ in loop['path']), 'arcs': loop['path'], 'length': loop['length']}
        if (loop['length'] < lead.spec.length_m+2*c+1 or not committed_before(lead, start-c)
                or stops_inside(lead, start, end) or not track_free(engine, lead, track)):
            continue
        du = remaining_vertex(engine, follow, loop['u'])
        dv = remaining_vertex(engine, follow, loop['v'])
        if du is None or dv is None or dv <= du or stops_inside(follow, du, dv):
            continue
        speed = min(edge_speed(engine, lead, eid) for eid, _ in loop['path'])
        enter = tl.at(start)
        extra = max(0, loop['length']/speed-(tl.times[loop['b']]-tl.times[loop['a']]))
        clear_u, at_hold = enter+(lead.spec.length_m+c)/speed, enter+(loop['length']-c)/speed
        arrive = tf.at(du)
        # The follower trails the leader by about one block before the loop.
        go = max(arrive, clear_u+engine.config.signal_block_m/tf.speeds[tf.edge(du)])
        f_delay = go-arrive
        f_clear_v = go+tf.at(dv+follow.spec.length_m+c)-arrive
        wait = max(0, f_clear_v-at_hold)
        l_delay = wait+(restart_s(lead) if wait > 1 else 0)+extra
        cost = delay_cost(engine, lead, tl, l_delay)+delay_cost(engine, follow, tf, f_delay)
        result.append(option(f'Overtake at loop {loop["name"]}: {lead.spec.id} takes the loop, {follow.spec.id} overtakes',
            'overtake', cost, {lead.spec.id: l_delay, follow.spec.id: f_delay},
            yield_id=lead.spec.id, through_id=follow.spec.id, loop=loop))
    return result


def evaluate_following(engine, lead, follow, span):
    tl, tf = timeline(engine, lead), timeline(engine, follow)
    if tf.at(tf.lengths[span[0][1]])-engine.sim_time > HORIZON_S:
        return None
    k_lead, k_follow = span[-1]
    lead_clear = tl.at(tl.lengths[k_lead+1]+lead.spec.length_m+engine.config.clearance_m)
    headway = engine.config.signal_block_m/tf.speeds[k_follow]
    delay = max(0, lead_clear+headway-tf.at(tf.lengths[k_follow+1]))
    if delay < MIN_CONFLICT_S:
        return None
    options = [option(f'{follow.spec.id} follows {lead.spec.id} without overtaking', 'follow',
        delay_cost(engine, follow, tf, delay), {follow.spec.id: delay, lead.spec.id: 0},
        yield_id=follow.spec.id, through_id=lead.spec.id)]
    return options+overtake_options(engine, lead, follow, tl, tf, span)


def apply_overtake(engine, best, options):
    lead, follow = engine.trains[best['yield_id']], engine.trains[best['through_id']]
    c = engine.config.clearance_m
    record = {'time_s': round(engine.sim_time, 1), 'conflict': 'overtake', 'trains': sorted([lead.spec.id, follow.spec.id]),
        'chosen': best['label'], 'kind': best['kind'],
        'options': [{k: o[k] for k in ('label', 'kind', 'cost', 'delays_s')} for o in sorted(options, key=lambda o: o['cost'])]}
    if best['kind'] == 'overtake':
        loop = best['loop']
        start = engine.network.lengths(lead.route)[loop['a']]
        before = [a[0] for a in lead.route]
        reroute_through(engine, lead, loop)
        hold = start+loop['length']-c
        if lead.authority is not None:
            # The loop path is validated by the next grant, not assumed.
            lead.commit_to = lead.authority
        lead.section_end = None
        reserve(engine, lead, {'edges': frozenset(e for e, _ in loop['path']), 'name': f'loop {loop["name"]}'}, loop['v'], hold)
        lead.hold = {'distance': hold, 'partner': follow.spec.id, 'vertex': loop['v'], 'kind': 'overtake',
            'label': f'waits in loop {loop["name"]} while {follow.spec.id} overtakes'}
        engine.log('reroute', f'{lead.spec.id} routed through existing loop {loop["name"]} for an overtake by {follow.spec.id}',
            [lead.spec.id, follow.spec.id], before=before, after=[a[0] for a in lead.route])
    else:
        record['chosen'] += ' (no intervention)'
    log_decision(engine, record)


def release_holds(engine):
    changed = False
    for t in engine.trains.values():
        h = t.hold
        if h is None:
            continue
        partner = engine.trains.get(h['partner'])
        if partner is None or partner.state == 'completed' or remaining_vertex(engine, partner, h['vertex']) is None:
            engine.log('plan', f'{t.spec.id} dispatcher hold released: {h["label"]}', [t.spec.id, h['partner']], before=h)
            t.hold = None
            changed = True
    return changed


def prepare(engine, claims):
    """Per-plan indexes: claimed and committed edges, reservations, timelines."""
    from . import dispatch
    engine._timelines = {}
    engine._claimed_edges = {}
    for r, owner in claims.items():
        eid = resource_edge(r)
        if eid is not None:
            engine._claimed_edges.setdefault(eid, set()).add(owner)
    engine._committed_edges = {}
    for t in engine.trains.values():
        if t.launched and t.state != 'completed':
            for r in dispatch.movements(engine, t, committed_limit(t) if t.authority is not None else t.x):
                eid = resource_edge(r)
                if eid is not None:
                    engine._committed_edges.setdefault(eid, set()).add(t.spec.id)
    engine._footprints = {t.spec.id: dispatch.resources(engine, t, physical=True) for t in engine.trains.values()}
    engine._route_arcs = {t.spec.id: set(t.route) for t in engine.trains.values()}
    index_trains(engine, [t for t in engine.trains.values() if t.state not in {'arrived', 'completed'}])
    release_tracks(engine)


def plan_conflicts(engine):
    """Plan overtakes for adjacent leader/follower pairs; True if a plan changed."""
    if not engine.config.auto_dispatch:
        return False
    active = [t for t in engine.trains.values() if t.state not in {'arrived', 'completed'}]
    index_trains(engine, active)
    users = {}
    for t in active:
        for eid, _ in t._ahead:
            users.setdefault(eid, set()).add(t.spec.id)
    pairs = sorted({tuple(sorted((x, y))) for group in users.values() for x in group for y in group if x < y})
    leader = {}
    for x, y in pairs:
        a, b = engine.trains[x], engine.trains[y]
        span = shared_arcs(a, b)
        if not span:
            continue
        ta, tb = timeline(engine, a), timeline(engine, b)
        ma, mb = ta.at(ta.lengths[span[0][0]]), tb.at(tb.lengths[span[0][1]])
        if abs(ma-mb) < MERGE_MARGIN_S and not (a.launched or b.launched):
            # Simultaneous departures are ordered by dispatch priority, not overtaken.
            continue
        lead, follow, merge = (a, b, ma) if ma <= mb else (b, a, mb)
        # Only the immediate leader is considered; farther leaders come later.
        if follow.spec.id not in leader or merge > leader[follow.spec.id][0]:
            leader[follow.spec.id] = (merge, lead)
    changed = False
    for follow_id, (_, lead) in sorted(leader.items(), key=lambda item: (item[1][0], item[0])):
        follow = engine.trains[follow_id]
        if lead.hold is not None or follow.hold is not None or frozenset((lead.spec.id, follow_id)) in engine.cancelled_pairs:
            continue
        span = shared_arcs(lead, follow)
        options = evaluate_following(engine, lead, follow, span) if span else None
        if not options:
            continue
        best = min(options, key=lambda o: (o['cost'], o['kind'] != 'follow', o['label']))
        if best['kind'] == 'follow':
            note = (lead.spec.id, follow_id, best['label'])
            if note not in engine.noted_conflicts:
                engine.noted_conflicts.add(note)
                apply_overtake(engine, best, options)
            continue
        apply_overtake(engine, best, options)
        changed = True
        index_trains(engine, (lead, follow))
    return changed
