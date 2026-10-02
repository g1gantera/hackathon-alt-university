"""Progressive movement authority with exclusive blocks and directional route locks."""
import time

from . import meets
from .blocks import resource_edge, resource_label, route_blocks


def score(engine,t):
    w=engine.config.weights
    arrival=engine.eta(t)
    target=t.spec.scheduled_arrival_s
    delay=0 if target is None else max(0,(engine.sim_time if arrival is None else arrival)-target)
    energy=engine.route_energy(t)
    parts={'importance':w.importance*(t.spec.importance-1)/9,'delay':w.delay*min(delay/300,1),'waiting':w.waiting*t.waiting/300,'energy':-w.energy*min(energy/1000,1)}
    return sum(parts.values()),parts


def resources(engine,t,until=None,physical=False):
    if not t.launched or t.state=='completed':
        return set()
    net=engine.network
    rear=t.x-t.spec.length_m-engine.config.clearance_m
    front=t.x if physical else (until if until is not None else t.authority if t.authority is not None else t.x)
    lengths=net.lengths(t.route)
    result=set()
    for block in route_blocks(engine,t):
        if block['entry'] <= front+1e-7 and block['exit'] >= rear-1e-7:
            result.add(block['resource'])
    for i,_ in enumerate(t.route):
        if rear-1e-7 <= lengths[i] <= front+1e-7:
            result.add(f'v:{net.startpoint(t.route[i])}')
    if rear<=lengths[-1]<=front+1e-7:
        result.add(f'v:{net.endpoint(t.route[-1])}')
    return result


def movements(engine,t,until):
    """Resources, entry distances and directed traversals, including staged trains.

    A route commitment may extend past movement authority. Compatible trains can
    share that commitment, but actual block and junction claims stay exclusive.
    """
    net=engine.network
    lengths=net.lengths(t.route)
    rear=t.x-t.spec.length_m-engine.config.clearance_m
    result={}
    for block in route_blocks(engine,t):
        if block['entry']<=until+1e-7 and block['exit']>=rear-1e-7:
            result[block['resource']]=(block['entry'],frozenset([(block['edge'],block['direction'])]))
    for i,arc in enumerate(t.route):
        if rear-1e-7<=lengths[i]<=until+1e-7:
            result[f'v:{net.startpoint(arc)}']=(lengths[i],frozenset(t.route[max(0,i-1):i+1]))
    if rear<=lengths[-1]<=until+1e-7:
        result[f'v:{net.endpoint(t.route[-1])}']=(lengths[-1],frozenset([t.route[-1]]))
    return result


def incompatible_routes(t,intended,commitments):
    """Opposing edges and crossing junction movements cannot share a commitment."""
    conflicts={}
    for owner,route in commitments.items():
        if owner==t.spec.id:
            continue
        for r in intended.keys() & route.keys():
            if not intended[r][1] & route[r][1]:
                conflicts[r]=owner
    return conflicts


def rolling_limit(engine,t):
    """Protect a local braking horizon, ending before a logical block boundary.

    Kazakhstan's automatic-block rules permit entry block by block, not an
    exclusive reservation to a distant destination. The numerical horizon and
    assumed signal positions are simulator choices, not prescribed KTZ values.
    """
    target=t.x+meets.horizon_m(engine,t)
    stop=meets.commit_limit(t)
    if target>=stop:
        return stop
    for block in route_blocks(engine,t):
        boundary=block['exit']-engine.config.clearance_m
        if boundary>=target:
            return min(stop,boundary)
    return stop


def available_authority(engine,t,intended,claims,closed,occupied=None):
    """Stop before the first unavailable block/junction; never revoke a grant."""
    physical=resources(engine,t,physical=True)
    horizon=rolling_limit(engine,t)
    obstacles=[]
    for r,(entry,_) in intended.items():
        if entry>horizon+1e-7:
            continue
        owner=claims.get(r)
        if owner is not None and owner!=t.spec.id:
            obstacles.append((entry,r,owner))
        elif resource_edge(r) in closed and r not in physical:
            obstacles.append((entry,r,None))
    if not obstacles:
        return horizon,[],''
    entry=min(item[0] for item in obstacles)
    first=[item for item in obstacles if abs(item[0]-entry)<1e-7]
    blockers=sorted({owner for _,_,owner in first if owner is not None})
    occupied=occupied or {}
    details=[]
    for _,resource,owner in sorted(first,key=lambda item:item[1]):
        asset=resource_label(resource)
        details.append(f'{asset} occupied by {owner}' if owner and occupied.get(resource)==owner
            else f'{asset} reserved ahead for {owner}' if owner
            else f'{asset} closed or failed')
    return min(horizon,entry-engine.config.clearance_m),blockers,'; '.join(details)


def clearance_wait(engine,t,other):
    """Approximate the relevant resource's release, not the leader's whole trip."""
    if other.state=='completed':
        return 0
    if other.state=='arrived':
        return max(0,other.dwell_until-engine.sim_time)
    intended=movements(engine,t,t.next_stop['distance'])
    committed=movements(engine,other,meets.committed_limit(other) if other.authority is not None else other.x)
    incompatible=[r for r in intended.keys() & committed.keys() if not intended[r][1] & committed[r][1]]
    if incompatible:
        relevant=incompatible
    else:
        shared=intended.keys() & resources(engine,other)
        if not shared:
            return 0
        first=min(intended[r][0] for r in shared)
        relevant=[r for r in shared if abs(intended[r][0]-first)<1e-7]
    net=engine.network
    exits={b['resource']:b['exit'] for b in route_blocks(engine,other)}
    release=max(exits.get(r,committed[r][0])
        for r in relevant)+other.spec.length_m+engine.config.clearance_m
    target=min(release,other.total)
    lengths=net.lengths(other.route)
    travel=0
    for i,(eid,_) in enumerate(other.route):
        distance=max(0,min(target,lengths[i+1])-max(other.x,lengths[i]))
        speed=min(other.spec.max_speed_kmh,net.speed_limit(eid),engine.restriction(eid))/3.6
        travel+=distance/max(1,speed)
    dwell=sum(s['dwell'] for s in other.stop_points[other.stop_index:] if other.x<=s['distance']<release)
    recovery=max(0,other.dwell_until-engine.sim_time)
    for inc in engine.active_incidents():
        if inc['kind']!='speed_restriction' and other.spec.id in engine.affected_trains(inc):
            if inc['end_s'] is None:
                return None
            recovery=max(recovery,inc['end_s']-engine.sim_time)
    return travel+dwell+recovery


def wait_for_cycle(graph):
    """Visit each wait dependency once, including large acyclic wait graphs."""
    visited=set()
    active={}
    path=[]
    def visit(node):
        if node in active:
            return path[active[node]:]
        if node in visited:
            return []
        active[node]=len(path)
        path.append(node)
        for following in graph.get(node,[]):
            found=visit(following)
            if found:
                return found
        path.pop()
        del active[node]
        visited.add(node)
        return []
    return next((found for node in graph if (found:=visit(node))),[])


def plan(engine,reason='state update'):
    started=time.perf_counter()
    claims={}
    occupied={r:t.spec.id for t in engine.trains.values() for r in resources(engine,t,physical=True)}
    for t in engine.trains.values():
        for r in resources(engine,t):
            if r in claims and claims[r]!=t.spec.id:
                engine.log('safety','Conflicting claim detected; simulation paused',[t.spec.id,claims[r]])
                engine.running=False
                return
            claims[r]=t.spec.id
    meets.prepare(engine,claims)
    changed=meets.release_holds(engine)
    # Overtake planning runs on every event, after any hold release and
    # periodically on the clock; it may route a leader into a loop.
    if changed or reason!='clock' or engine.last_conflict_scan is None or engine.sim_time-engine.last_conflict_scan>=meets.SCAN_INTERVAL_S:
        engine.last_conflict_scan=engine.sim_time
        changed=meets.plan_conflicts(engine) or changed
    # Direction commitments prevent head-on admission beyond a short movement
    # grant. Against opposing traffic they end at the next passing point with
    # a reserved receiving track (see meets), otherwise at the next stop. A
    # dwell retains only the physical footprint, so a train inside a siding
    # lets others pass.
    commitments={t.spec.id:movements(engine,t,meets.committed_limit(t) if t.authority is not None else t.x)
        for t in engine.trains.values() if t.launched and t.state!='completed'}
    candidates=[t for t in engine.trains.values()
        if (t.authority is None or t.authority<t.next_stop['distance']-1e-7)
        and t.state not in {'arrived','completed'}
        and engine.sim_time>=t.spec.departure_s and engine.sim_time>=t.dwell_until]
    # Bounded preference followed by an explicit FIFO starvation gate.
    scores={t.spec.id:score(engine,t) for t in candidates}
    candidates.sort(key=lambda t:(0 if t.waiting>=engine.config.starvation_s else 1, -t.waiting if t.waiting>=engine.config.starvation_s else -scores[t.spec.id][0],t.spec.departure_s,t.spec.id))
    incident_edges=engine.closed_edges()
    for t in candidates:
        value,parts=scores[t.spec.id]
        t.dispatch_score=round(value,3)
        t.score_parts=parts
        blocked=engine.blocking_incidents(t)
        if blocked or (t.authority is not None and engine.incident_stop(t)[1]):
            if t.authority is None:
                engine.hold(t,'Incident: '+', '.join(blocked),[])
            continue
        if t.hold is not None and t.hold['distance']<=max(t.x,t.authority if t.authority is not None else t.x)+1e-7:
            detail='Dispatcher hold: '+t.hold['label']
            t.blockers=[t.hold['partner']] if t.hold['partner'] in engine.trains else []
            t.authority_reason=detail
            if t.authority is None:
                engine.hold(t,detail,t.blockers)
            elif t.state=='waiting':
                t.reason=detail
            continue
        section=meets.advance_section(engine,t,commitments)
        old_launched=t.launched
        intended=movements(engine,t,meets.commit_limit(t))
        incompatible=incompatible_routes(t,intended,commitments)
        limit,blockers,detail=available_authority(engine,t,intended,claims,incident_edges,occupied)
        # A following train with a usable clear prefix should keep its path.
        # Detouring around a distant occupied block can turn a compatible
        # following movement into a crossing approach at its destination.
        future_closure=t.authority is None and any(resource_edge(r) in incident_edges for r in intended)
        if incompatible or future_closure or (detail and (not blockers or limit<=t.x+1e-7)):
            # Only change an uncommitted path; occupied prefix and stops are immutable.
            if engine.try_reroute(t,{**claims,**incompatible}):
                changed=True
                section=meets.advance_section(engine,t,commitments)
                intended=movements(engine,t,meets.commit_limit(t))
                incompatible=incompatible_routes(t,intended,commitments)
                limit,blockers,detail=available_authority(engine,t,intended,claims,incident_edges,occupied)
        if incompatible:
            blockers=sorted(set(incompatible.values()))
            detail='Opposing or crossing route direction locked for '+', '.join(blockers)+' (protected to its next stop or passing loop)'
        elif t.hold is not None and not detail and limit>=t.hold['distance']-1e-6:
            blockers=[t.hold['partner']] if t.hold['partner'] in engine.trains else []
            detail='Dispatcher hold: '+t.hold['label']
        elif section is not None and not detail and limit>=meets.commit_limit(t)-1e-6:
            blockers,detail=section
        t.blockers=blockers
        t.authority_reason=detail
        if not incompatible and t.authority is not None:
            # Validated: the direction commitment may now grow to this limit.
            t.commit_to=meets.commit_limit(t)
            commitments[t.spec.id]=intended
        # Extension is monotonic, including during incidents and priority edits.
        if incompatible or limit<=max(t.x,t.authority if t.authority is not None else t.x)+1e-7:
            if t.authority is None:
                engine.hold(t,detail+'; waiting for a clear section',blockers)
            elif t.state=='waiting':
                t.reason=detail+'; waiting for section clearance'
            continue
        t.launched=True
        t.authority=limit
        t.state='running'
        target=(t.next_stop['label'] if limit==t.next_stop['distance']
            else f'dispatcher hold point at {limit:.1f} m ({t.hold["label"]})' if t.hold is not None and abs(limit-t.hold['distance'])<1e-6
            else f'passing point receiving track at {limit:.1f} m' if t.section_end is not None and abs(limit-t.section_end)<1e-6
            else f'section boundary at {limit:.1f} m')
        t.reason=f'Movement authorised to {target}; dispatch score {value:.2f}. '+('FIFO waiting protection.' if t.waiting>=engine.config.starvation_s else 'Highest feasible preference in this decision.')
        proposed=resources(engine,t)
        for r in proposed:
            claims[r]=t.spec.id
        commitments[t.spec.id]=intended
        t.commit_to=meets.commit_limit(t)
        for r in resources(engine,t,physical=True):
            occupied[r]=t.spec.id
        engine.log('departure' if not old_launched else 'plan',t.reason,[t.spec.id],after={'authority_m':t.authority,'score':value,'parts':parts})
        if not old_launched:
            t.actual_departure=engine.sim_time
        t.waiting=0
        changed=True
    # Detect a wait-for cycle; no switch or reservation is ever broken to resolve it.
    # Only stationary trains are waiting; a train still moving toward its
    # hold point or signal can release its partner's condition by progressing.
    graph={t.spec.id:t.blockers for t in candidates if t.speed<.1}
    # Trains others are waiting for skip timetable pacing (see ato.envelope).
    engine.blocking={b for t in candidates for b in t.blockers}
    deadlock=wait_for_cycle(graph)
    held=[tid for tid in deadlock if engine.trains[tid].hold is not None]
    if held:
        # Dispatcher holds are advisory plans; cancel them rather than let them
        # sustain a cycle. Locks and commitments are untouched, so this is safe.
        for tid in deadlock:
            t=engine.trains[tid]
            if t.hold is not None:
                engine.cancelled_pairs.add(frozenset((tid,t.hold['partner'])))
                engine.log('plan',f'{tid} dispatcher hold cancelled to break a wait-for cycle: {t.hold["label"]}',deadlock,before=t.hold)
                t.hold=None
        engine.last_conflict_scan=None
    if deadlock!=engine.deadlock:
        engine.log('conflict','Wait-for cycle: hold safely; clearance or removal at a stopped terminal is required' if deadlock else 'No wait-for cycle remains',deadlock)
    engine.deadlock=deadlock
    if changed or reason!='clock':
        engine.plan_version+=1
        engine.log('plan',f'Plan {engine.plan_version}: {reason}',after={'version':engine.plan_version})
    engine.update_signals()
    duration=(time.perf_counter()-started)*1000
    engine.metrics['replan_ms']=round(duration,3)
    engine.metrics['max_replan_ms']=max(engine.metrics.get('max_replan_ms',0),duration)
    engine.metrics['replans']+=1
