"""Authoritative fixed-step simulation; only the server advances time or positions."""
import math
import random
import time
import uuid
from dataclasses import dataclass, field

from . import ato, blocks, dispatch, meets
from .history import History
from .models import IncidentSpec, Settings, TrainSpec
from .quality import Quality


@dataclass
class Train:
    spec: TrainSpec
    route: list
    stop_points: list
    total: float
    x: float = 0
    speed: float = 0
    authority: float | None = None
    authority_reason: str = ''
    launched: bool = False
    state: str = 'scheduled'
    reason: str = 'Awaiting departure time and a compatible route'
    waiting: float = 0
    dwell_until: float = 0
    stop_index: int = 0
    energy: float = 0
    reference_energy: float = 0
    dispatch_score: float = 0
    score_parts: dict = field(default_factory=dict)
    blockers: list = field(default_factory=list)
    arrivals: list = field(default_factory=list)
    actual_departure: float | None = None
    trace: list = field(default_factory=list)
    reroute_signature: tuple | None = None
    emergency_incidents: set = field(default_factory=set)
    # Dispatcher hold from automatic conflict resolution (meet/overtake/order).
    hold: dict | None = None
    # Direction commitment validated at the last grant; it grows only through
    # dispatch checks, never because a hold was released or replaced.
    commit_to: float | None = None
    # End of the committed passing-point section (None: not yet computed).
    section_end: float | None = None
    # Receiving tracks reserved at passing points ahead or in use.
    tracks: list = field(default_factory=list)

    @property
    def next_stop(self):
        return self.stop_points[min(self.stop_index,len(self.stop_points)-1)]


class Engine:
    def __init__(self,network,history=None,config=None):
        self.network=network
        self.history=history or History()
        saved=self.history.load_settings()
        self.config=config or (Settings(**saved) if saved else Settings())
        self.trains={}
        self.incidents={}
        self.signals={}
        self.switches={}
        self.sim_time=0.0
        self.running=False
        self.plan_version=0
        self.state_version=0
        self.deadlock=[]
        self.quality=Quality()
        self.rng=random.Random(self.config.random_seed)
        self.last_plan=-1
        self.last_snapshot=-1
        self.last_quality_score=None
        self.metrics={'replan_ms':0,'max_replan_ms':0,'replans':0,'steps':0,'incident_batches':0,'max_incident_batch_ms':0,'rejected_ingest':0}
        self.ingest_sequences={}
        self.alternatives=[]
        self.demo=network.find_demo()
        self._incident_cache=None
        self.decisions=[]
        self.noted_conflicts=set()
        self.cancelled_pairs=set()
        self.tracks={}
        self.last_conflict_scan=None
        self.blocking=set()

    def log(self,kind,message,entities=(),actor='engine',before=None,after=None):
        return self.history.log(self.sim_time,kind,message,entities,actor,before,after)

    def reset(self,actor='dispatcher'):
        self.log('action','Simulation reset',actor=actor)
        self.trains.clear(); self.incidents.clear(); self.signals.clear(); self.switches.clear()
        self.touch_incidents()
        self.sim_time=0; self.running=False; self.deadlock=[]; self.alternatives=[]
        self.decisions=[]; self.noted_conflicts=set(); self.cancelled_pairs=set(); self.tracks={}; self.last_conflict_scan=None; self.blocking=set()
        self.plan_version+=1; self.state_version+=1
        self.history.run=uuid.uuid4().hex[:12]
        self.history.recent.clear()
        self.quality=Quality(); self.last_quality_score=None
        self.last_plan=-1; self.last_snapshot=-1
        self.rng=random.Random(self.config.random_seed)
        self.ingest_sequences.clear()
        self.log('action','New simulation run',actor=actor)

    def build_route(self,spec):
        n=self.network
        for v in [spec.origin,spec.destination,*[s.vertex for s in spec.stops]]:
            if v>=len(n.vertices):
                raise ValueError(f'Unknown vertex V{v}')
        route=[]; stops=[]; at=spec.origin
        if spec.via_siding is not None:
            if spec.via_siding not in n.allowed:
                raise ValueError('Siding is unavailable or depends on uncertain topology')
            e=n.edges[spec.via_siding]
            if e['category'] not in {'siding','yard'}:
                raise ValueError('Holding requires an existing siding or yard edge')
            if e['length_m'] < spec.length_m+2*self.config.clearance_m:
                raise ValueError(f'Train length {spec.length_m:g} m exceeds usable siding length {e["length_m"]-2*self.config.clearance_m:.2f} m')
            choices=[]
            for direction in (0,1):
                arc=(e['id'],direction)
                if not n.direction_allowed(arc):
                    continue
                first=n.route(at,n.startpoint(arc),{e['id']})
                if first is None or (first and not n.compatible(first[-1],arc)):
                    continue
                last=n.route(n.endpoint(arc),spec.destination,{e['id']},incoming=arc)
                if last is None:
                    continue
                choices.append((n.lengths(first+[arc]+last)[-1],first,arc,last))
            if not choices:
                raise ValueError('No connected, direction-compatible route through that siding')
            _,first,arc,last=min(choices,key=lambda c:c[0])
            route=first+[arc]+last
            stop_distance=n.lengths(first+[arc])[-1]-self.config.clearance_m
            stops.append(dict(distance=stop_distance,dwell=spec.give_way_s,scheduled=None,label=f'Siding E{e["id"]}',vertex=None))
        else:
            for stop in [*spec.stops, None]:
                vertex=stop.vertex if stop else spec.destination
                part=n.route(at,vertex,incoming=route[-1] if route else None)
                if part is None or not part:
                    raise ValueError(f'No verified route from V{at} to V{vertex}; gaps, disconnected tracks or incompatible turns are not repaired')
                route.extend(part)
                if stop:
                    stops.append(dict(distance=n.lengths(route)[-1],dwell=stop.dwell_s,scheduled=stop.scheduled_arrival_s,earliest_departure_s=stop.earliest_departure_s,label=n.label(vertex),vertex=vertex))
                at=vertex
        if len({a[0] for a in route})!=len(route):
            raise ValueError('A journey may not reverse or reuse a block; split it into separate services')
        total=n.lengths(route)[-1]
        if total<spec.length_m:
            raise ValueError('Train is longer than this journey; choose farther endpoints')
        stops.append(dict(distance=total,dwell=self.config.terminal_release_s,scheduled=spec.scheduled_arrival_s,label=n.label(spec.destination),vertex=spec.destination))
        return route,stops,total

    def add_train(self,spec,actor='dispatcher',replan=True):
        if spec.id in self.trains:
            raise ValueError('Train ID already exists')
        if len(self.trains)>=100:
            raise ValueError('Demo limit is 100 trains')
        route,stops,total=self.build_route(spec)
        t=Train(spec,route,stops,total)
        self.trains[spec.id]=t
        self.log('creation',f'{spec.name} created: {self.network.label(spec.origin)} → {self.network.label(spec.destination)}',[spec.id],actor,after=spec.model_dump())
        if replan:
            self.replan('train added')
        return t

    def hold(self,t,reason,blockers):
        if t.reason!=reason:
            self.log('conflict',reason,[t.spec.id,*blockers],before=t.reason,after=reason)
        t.reason=reason; t.blockers=blockers; t.state='waiting'

    def touch_incidents(self):
        """Invalidate derived incident state after any status change."""
        self._incident_cache=None

    def incident_state(self):
        # Hot path: physics, ATO and signals query these per block and step.
        if getattr(self,'_incident_cache',None) is None:
            active=[i for i in self.incidents.values() if i['status']=='active']
            closed=frozenset().union(*(self.incident_edges(i) for i in active if i['kind'] in {'track_closure','signal_failure','train_delay'}))
            restrictions={}
            for i in active:
                if i['kind']=='speed_restriction':
                    for eid in self.incident_edges(i):
                        restrictions[eid]=min(restrictions.get(eid,160),i['speed_kmh'])
            self._incident_cache=(active,closed,restrictions)
        return self._incident_cache

    def active_incidents(self):
        return self.incident_state()[0]

    def incident_edges(self,i):
        kind=i['asset_type']; asset=i['asset_id']; n=self.network
        if kind=='edge':
            return {int(asset)}
        if kind=='signal':
            # The supplied map has no certified signal/interlocking inventory.
            # Failures therefore close the parent track edge conservatively,
            # including when the selected signal is a simulated internal block.
            return {int(asset.split('-')[1])}
        if kind in {'station','switch'}:
            return set(n.adj[int(asset)])
        return set()

    def closed_edges(self):
        return self.incident_state()[1]

    def restriction(self,eid):
        return self.incident_state()[2].get(eid,160)

    def blocking_incidents(self,t):
        active=[]
        for i in self.active_incidents():
            if i['asset_type']=='train' and i['asset_id']==t.spec.id:
                active.append(i['id'])
            elif not t.launched and t.route[0][0] in self.incident_edges(i) and i['kind']!='speed_restriction':
                active.append(i['id'])
        return active

    def incident_stop(self,t):
        stop=None; emergency=False
        lengths=self.network.lengths(t.route)
        active=self.active_incidents()
        t.emergency_incidents.intersection_update(i['id'] for i in active)
        for inc in active:
            if inc['asset_type']=='train' and inc['asset_id']==t.spec.id:
                emergency=True
            if inc['id'] in t.emergency_incidents:
                emergency=True
            if inc['kind'] not in {'track_closure','signal_failure','train_delay'}:
                continue
            edges=self.incident_edges(inc)
            for i,arc in enumerate(t.route):
                if arc[0] not in edges or lengths[i+1]<t.x-t.spec.length_m-1e-6:
                    continue
                if t.launched and lengths[i] <= t.x <= lengths[i+1]+1e-6:
                    # A head inside requires a complete stop, latched even if
                    # it overruns a short block while braking. A consist whose
                    # head has already left keeps moving to clear the section
                    # instead of standing inside it until clearance.
                    t.emergency_incidents.add(inc['id'])
                    emergency=True
                elif lengths[i]>t.x:
                    boundary=max(t.x,lengths[i]-self.config.clearance_m)
                    stop=boundary if stop is None else min(stop,boundary)
        return stop,emergency

    def validate_incident(self,spec):
        n=self.network
        if spec.asset_type=='train':
            if spec.asset_id not in self.trains:
                raise ValueError('Unknown train asset')
        elif spec.asset_type=='signal':
            parts=spec.asset_id.split('-')
            if len(parts) not in {3,4} or parts[0]!='SIG' or not parts[1].isdigit() or parts[2] not in {'0','1'} or int(parts[1]) not in n.allowed:
                raise ValueError('Signal ID must identify an existing simulated block signal')
            if len(parts)==4:
                eid,direction=int(parts[1]),int(parts[2])
                count=blocks.block_count(self,eid)
                origin_index=0 if direction==0 else count-1
                if not parts[3].isdigit() or not 0<=int(parts[3])<count or int(parts[3])==origin_index:
                    raise ValueError('Unknown internal block signal')
        else:
            try:
                asset=int(spec.asset_id)
            except ValueError:
                raise ValueError('This asset requires a numeric ID') from None
            if spec.asset_type=='edge' and asset not in n.allowed:
                raise ValueError('Unknown or excluded edge')
            if spec.asset_type=='station' and asset not in n.station_at:
                raise ValueError('Station asset ID is its mapped vertex')
            if spec.asset_type=='switch' and (asset<0 or asset>=len(n.vertices) or len(n.adj[asset])<3):
                raise ValueError('Unknown switch vertex')
        if spec.id and spec.id in self.incidents:
            raise ValueError('Incident ID already exists')

    def add_incidents(self,specs,actor='dispatcher'):
        start=time.perf_counter()
        for spec in specs:
            self.validate_incident(spec)
        ids=[s.id for s in specs if s.id]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate incident IDs in batch')
        result=[]
        for spec in specs:
            data=spec.model_dump()
            data['id']=spec.id or 'INC-'+uuid.uuid4().hex[:8]
            data['start_s']=self.sim_time if spec.start_s is None else spec.start_s
            data['end_s']=None if spec.duration_s is None else data['start_s']+spec.duration_s
            data['status']='scheduled' if data['start_s']>self.sim_time else 'active'
            data['affected_trains']=self.affected_trains(data)
            data['created_at_s']=self.sim_time
            self.incidents[data['id']]=data
            self.touch_incidents()
            result.append(data)
            self.log('incident',f'{data["kind"]}: {data["asset_type"]} {data["asset_id"]}',[data['id'],*data['affected_trains']],actor,after=data.copy())
        self.metrics['incident_batches']+=1
        self.replan(f'{len(specs)} incident(s) ingested')
        elapsed=(time.perf_counter()-start)*1000
        self.metrics['last_incident_batch_ms']=round(elapsed,3)
        self.metrics['max_incident_batch_ms']=max(self.metrics['max_incident_batch_ms'],elapsed)
        return result

    def affected_trains(self,inc):
        if inc['asset_type']=='train':
            return [inc['asset_id']]
        edges=self.incident_edges(inc)
        result=[]
        for t in self.trains.values():
            if t.state=='completed':
                continue
            index,_,_=self.network.locate(t.route,t.x)
            physical=dispatch.resources(self,t,physical=True)
            if inc['id'] in t.emergency_incidents or any(a[0] in edges for a in t.route[index:]) or edges & blocks.block_edges(physical):
                result.append(t.spec.id)
        return result

    def clear_incident(self,iid,actor='dispatcher'):
        if iid not in self.incidents:
            raise ValueError('Unknown incident')
        data=self.incidents[iid]; before=data.copy()
        if data['status']=='cleared':
            return
        data['status']='cleared'; data['cleared_s']=self.sim_time
        self.touch_incidents()
        self.log('clearance',f'{iid} cleared',[iid],actor,before,data.copy())
        self.replan('incident cleared')

    def try_reroute(self,t,claims):
        if t.authority is not None or t.spec.via_siding is not None or t.hold is not None:
            return False
        blocked_edges=blocks.block_edges(r for r,owner in claims.items() if owner!=t.spec.id)|self.closed_edges()
        blocked_vertices={int(r[2:]) for r,owner in claims.items() if r.startswith('v:') and owner!=t.spec.id}
        signature=(tuple(sorted(blocked_edges)),tuple(sorted(blocked_vertices)),t.stop_index)
        if signature==t.reroute_signature:
            return False
        t.reroute_signature=signature
        prefix=[]
        if t.launched:
            idx,_,_=self.network.locate(t.route,t.x)
            prefix=t.route[:idx+1]
        at=self.network.endpoint(prefix[-1]) if prefix else t.spec.origin
        route=prefix[:]
        new_stops=[s.copy() for s in t.stop_points[:t.stop_index]]
        for s in t.stop_points[t.stop_index:]:
            if s['vertex'] is None:
                return False
            part=self.network.route(at,s['vertex'],blocked_edges,blocked_vertices,incoming=route[-1] if route else None)
            if part is None:
                return False
            route.extend(part)
            new_stops.append({**s,'distance':self.network.lengths(route)[-1]})
            at=s['vertex']
        if not route or route==t.route or len({a[0] for a in route})!=len(route):
            return False
        if self.network.lengths(route)[-1]<t.spec.length_m:
            return False
        before=[a[0] for a in t.route]
        t.route=route; t.stop_points=new_stops; t.total=self.network.lengths(route)[-1]
        # Sections and receiving tracks ahead belong to the old path.
        physical=blocks.block_edges(dispatch.resources(self,t,physical=True))
        t.tracks=[r for r in t.tracks if physical & set(r['edges'])]
        t.section_end=None
        self.log('reroute','Feasible alternative uses existing connected edges; occupied prefix preserved',[t.spec.id],before=before,after=[a[0] for a in route])
        return True

    def route_energy(self,t,distance=None):
        d=max(0,t.total-t.x) if distance is None else distance
        v=min(t.spec.max_speed_kmh,80)/3.6
        return ato.energy_kwh(t.spec.mass_t,d,0,v)

    def eta(self,t):
        if t.arrivals and t.state in {'arrived','completed'}:
            return t.arrivals[-1]['time']
        remaining=max(0,t.total-t.x)
        lengths=self.network.lengths(t.route)
        travel=0
        for i,(eid,_) in enumerate(t.route):
            d=max(0,lengths[i+1]-max(t.x,lengths[i]))
            v=min(t.spec.max_speed_kmh,self.network.speed_limit(eid),self.restriction(eid))/3.6
            travel+=d/max(1,v)
        accel=min(t.spec.max_speed_kmh/3.6,20)/t.spec.acceleration_mps2/2 if t.speed<1 and remaining else 0
        dwell=sum(s['dwell'] for s in t.stop_points[t.stop_index:-1])
        recovery=0
        for inc in self.active_incidents():
            if t.spec.id in self.affected_trains(inc) and inc['kind']!='speed_restriction':
                if inc['end_s'] is None:
                    return None
                recovery=max(recovery,inc['end_s']-self.sim_time)
        if t.blockers:
            waits=[dispatch.clearance_wait(self,t,self.trains[b]) for b in t.blockers if b in self.trains]
            if any(wait is None for wait in waits):
                return None
            recovery=max(recovery,max(waits,default=0))
        return round(max(self.sim_time,t.spec.departure_s,t.dwell_until)+travel+accel+dwell+recovery,1)

    def delay(self,t,predicted=...):
        scheduled=t.spec.scheduled_arrival_s
        if scheduled is None:
            return 0
        if predicted is ...:
            predicted=self.eta(t)
        return max(0,(predicted if predicted is not None else self.sim_time)-scheduled)

    def update_signals(self):
        claims={r:t.spec.id for t in self.trains.values() for r in dispatch.resources(self,t)}
        occupied={r:t.spec.id for t in self.trains.values() for r in dispatch.resources(self,t,physical=True)}
        closed=self.closed_edges()
        failed_edges=set().union(*(self.incident_edges(inc) for inc in self.active_incidents() if inc['kind']=='signal_failure'))
        signals={}; switches={}
        for t in self.trains.values():
            route_blocks=blocks.route_blocks(self,t)
            incident_stop,emergency=self.incident_stop(t)
            effective_authority=t.authority if t.authority is not None else t.x
            if incident_stop is not None:
                effective_authority=min(effective_authority,incident_stop)
            if emergency:
                effective_authority=t.x
            # Count consecutive physically clear blocks within this train's
            # grant. A retained lock or an occupied block never clears a signal.
            permitted=[]
            for block in route_blocks:
                vertex=block['vertex']
                permitted.append(claims.get(block['resource'])==t.spec.id
                    and block['resource'] not in occupied and block['edge'] not in closed
                    and t.authority is not None
                    and t.x-1e-7<=block['entry']<effective_authority-1e-7
                    and (vertex is None or claims.get(f'v:{vertex}') in {None,t.spec.id}))
            clear_counts=[0]*len(route_blocks)
            for i in range(len(route_blocks)-1,-1,-1):
                if permitted[i]:
                    clear_counts[i]=1+(clear_counts[i+1] if i+1<len(route_blocks) else 0)
            for i,block in enumerate(route_blocks):
                eid,direction,vertex=block['edge'],block['direction'],block['vertex']
                sid=block['signal_id']
                owner=claims.get(block['resource'])
                authorised=permitted[i]
                failed=eid in failed_edges
                aspect='red'
                available=max(0,effective_authority-block['entry'])
                if authorised:
                    # Two tiny graph sections alone are insufficient warning:
                    # the remaining authority must also cover service braking.
                    speed=max(t.speed,min(t.spec.max_speed_kmh,self.network.speed_limit(eid),self.restriction(eid))/3.6)
                    braking_distance=speed**2/(2*t.spec.braking_mps2)+speed*1.2+self.config.clearance_m
                    aspect='green' if clear_counts[i]>=2 and available>=braking_distance and self.restriction(eid)>=self.network.speed_limit(eid) else 'yellow'
                candidate={'id':sid,'edge':eid,'direction':direction,'vertex':vertex,
                    'resource':block['resource'],'block_index':block['index'],
                    'aspect':aspect,'owner':owner,'failed':failed,
                    'occupied_by':occupied.get(block['resource']),
                    'clear_blocks_ahead':clear_counts[i],
                    'available_distance_m':round(available,2),
                    'position':block['position'],
                    'reason':'Failed: stop' if failed else 'Block occupied: stop' if block['resource'] in occupied else 'Clear block; approach stop or restriction' if aspect=='yellow' else 'At least two clear blocks; braking distance available' if aspect=='green' else 'No compatible authority / closed'}
                # A route in the opposing direction must not green another train's signal.
                if sid not in signals or owner==t.spec.id:
                    signals[sid]=candidate
            for i,arc in enumerate(t.route):
                eid=arc[0]; vertex=self.network.startpoint(arc)
                if len(self.network.adj[vertex])>=3 and claims.get(f'v:{vertex}')==t.spec.id:
                    switches[str(vertex)]={'vertex':vertex,'owner':t.spec.id,'position':[t.route[i-1][0] if i else None,eid],'locked':True}
        for sid,s in signals.items():
            old=self.signals.get(sid)
            if old is None or (old['aspect'],old['failed'],old['owner'])!=(s['aspect'],s['failed'],s['owner']):
                self.log('signal',f'{sid} → {s["aspect"]}',[sid],before=old,after=s)
        for sid in self.switches.keys()|switches.keys():
            if self.switches.get(sid)!=switches.get(sid):
                self.log('switch',f'V{sid} route lock changed',[sid],before=self.switches.get(sid),after=switches.get(sid))
        self.signals=signals; self.switches=switches

    def replan(self,reason):
        if reason!='clock':
            self.compute_alternatives()
        dispatch.plan(self,reason)

    def compute_alternatives(self):
        choices=[]
        for inc in self.active_incidents():
            affected=self.affected_trains(inc)
            inc['affected_trains']=affected
            for tid in affected:
                t=self.trains[tid]
                recovery=None if inc['end_s'] is None else max(0,inc['end_s']-self.sim_time)
                eta=self.eta(t)
                row={'incident_id':inc['id'],'train_id':tid,'actions':[], 'reason':'','evaluated_at_s':self.sim_time,'plan_version':self.plan_version+1}
                delay=None if eta is None or t.spec.scheduled_arrival_s is None else max(0,eta-t.spec.scheduled_arrival_s)
                impact=None if recovery is None or delay is None else round(-30*(min(delay/300,1)-min(max(0,delay-recovery)/300,1))/max(1,len(self.trains)),2)
                row['actions'].append({'action':'hold / retain authority' if inc['kind']!='speed_restriction' else 'apply restricted speed','arrival_s':eta,'delay_s':delay,'recovery_s':recovery,'quality_schedule_delta':impact,'recommended':True,'note':'Schedule-factor forecast vs immediate clearance; other measured factors held fixed. Not a measured improvement.'})
                if not t.launched and t.spec.via_siding is None and not t.spec.stops:
                    other_claims={r:o.spec.id for o in self.trains.values() if o.spec.id!=tid for r in dispatch.resources(self,o)}
                    banned=self.closed_edges()|blocks.block_edges(other_claims)
                    vertices={int(r[2:]) for r in other_claims if r.startswith('v:')}
                    path=self.network.route(t.spec.origin,t.spec.destination,banned,vertices)
                    if path and path!=t.route:
                        duration=sum(self.network.edges[e]['length_m']/(min(t.spec.max_speed_kmh,self.network.speed_limit(e))/3.6) for e,_ in path)
                        arrival=max(self.sim_time,t.spec.departure_s)+duration+t.spec.max_speed_kmh/3.6/t.spec.acceleration_mps2/2
                        row['actions'].append({'action':'reroute over existing edges','route_edges':[a[0] for a in path],'arrival_s':round(arrival,1),'delay_s':max(0,arrival-t.spec.scheduled_arrival_s) if t.spec.scheduled_arrival_s is not None else None,'recovery_s':round(duration,1),'quality_schedule_delta':None if delay is None else round(30*(min(delay/300,1)-min(max(0,arrival-(t.spec.scheduled_arrival_s or arrival))/300,1))/max(1,len(self.trains)),2),'recommended':eta is None or arrival<eta,'note':'Feasible against present locks; revalidated before execution.'})
                        if row['actions'][-1]['recommended']:
                            row['actions'][0]['recommended']=False
                row['reason']='Only hold / recovery is feasible: committed routes cannot be invalidated, or no compatible alternate track is available.' if len(row['actions'])==1 else 'Compare feasible detour travel time with clearance wait; dispatch validates locks before committing.'
                choices.append(row)
        if choices!=self.alternatives:
            self.log('alternatives',f'{len(choices)} affected train/incident evaluations',after=choices)
        self.alternatives=choices

    def advance(self,seconds):
        remaining=seconds
        while remaining>1e-8:
            dt=min(.2,remaining)
            self.step(dt)
            remaining-=dt

    def step(self,dt):
        self.sim_time+=dt
        self.metrics['steps']+=1
        incident_changed=False
        for inc in self.incidents.values():
            if inc['status']=='scheduled' and self.sim_time>=inc['start_s']:
                inc['status']='active'; incident_changed=True; self.touch_incidents()
                self.log('incident',f'{inc["id"]} activated',[inc['id']],after=inc.copy())
            if inc['status']=='active' and inc['end_s'] is not None and self.sim_time>=inc['end_s']:
                inc['status']='cleared'; inc['cleared_s']=self.sim_time; incident_changed=True; self.touch_incidents()
                self.log('clearance',f'{inc["id"]} duration elapsed',[inc['id']],after=inc.copy())
        if self.config.random_incidents_per_hour and self.rng.random()<1-math.exp(-self.config.random_incidents_per_hour*dt/3600):
            trains=sorted(t.spec.id for t in self.trains.values() if t.state not in {'completed','arrived'})
            if trains:
                tid=self.rng.choice(trains)
                self.add_incidents([IncidentSpec(id=f'RND-{self.metrics["steps"]}',kind=self.rng.choice(['train_delay','train_breakdown']),asset_type='train',asset_id=tid,duration_s=self.rng.randint(30,120))],actor='seeded-generator')
        changed=False
        for t in self.trains.values():
            if t.state=='arrived' and self.sim_time>=t.dwell_until:
                t.state='completed'; changed=True
                self.log('removal','Terminal service completed; consist withdrawn from simulation and occupied blocks released',[t.spec.id])
                continue
            if t.state in {'arrived','completed'}:
                continue
            if t.authority is None:
                if self.sim_time>=max(t.spec.departure_s,t.dwell_until):
                    t.waiting+=dt
                continue
            target=ato.envelope(self,t)
            old=t.speed
            speed=min(old+t.spec.acceleration_mps2*dt,target) if target>=old else max(target,old-t.spec.braking_mps2*dt)
            stop=t.authority
            incident_stop,emergency=self.incident_stop(t)
            if incident_stop is not None and old**2/(2*t.spec.braking_mps2)<=max(0,incident_stop-t.x)+1e-7:
                stop=min(stop,incident_stop)
            # Keep enough room to brake after this step, including acceleration
            # during the step. A newly imposed, unreachable incident boundary
            # never causes an instantaneous speed change or position clamp.
            brake=t.spec.braking_mps2*dt
            safe_next=max(0,math.sqrt(max(0,(brake/2)**2+2*t.spec.braking_mps2*max(0,stop-t.x)-brake*old))-brake/2)
            speed=max(0,old-brake,min(speed,safe_next))
            # If rest is reached before the step ends, spend the balance stopped.
            motion_dt=min(dt,old/t.spec.braking_mps2) if speed==0 and old>0 else dt
            delta=(old+speed)/2*motion_dt
            # The stop target is mandatory. Integration clips only the final sub-metre.
            delta=min(delta,max(0,stop-t.x))
            t.x+=delta; t.speed=speed
            first_motion=t.energy==0 and delta>0
            t.energy+=ato.energy_kwh(t.spec.mass_t,delta,old,speed)
            idx,arc,_=self.network.locate(t.route,t.x)
            reference_speed=min(t.spec.max_speed_kmh,self.network.speed_limit(arc[0]))/3.6
            t.reference_energy+=ato.energy_kwh(t.spec.mass_t,delta,reference_speed,reference_speed)
            if first_motion:
                t.reference_energy+=ato.energy_kwh(t.spec.mass_t,0,0,reference_speed)
            if t.authority-t.x<.05 and old<=t.spec.braking_mps2*dt+1e-8:
                t.x=t.authority; t.speed=0
                if t.authority<t.next_stop['distance']-1e-7:
                    # A red signal is not a timetable stop. Keep the grant and
                    # route commitment so clearance can extend it automatically.
                    changed=changed or t.state!='waiting'
                    t.state='waiting'
                    t.reason=t.authority_reason+'; waiting for section clearance'
                    t.waiting+=dt
                    continue
                arrived=t.next_stop
                t.arrivals.append({'label':arrived['label'],'time':round(self.sim_time,2),'scheduled':arrived['scheduled'],'error_m':round(t.x-arrived['distance'],4)})
                t.dwell_until=max(self.sim_time+arrived['dwell'],arrived.get('earliest_departure_s',0))
                t.authority=None
                t.commit_to=None
                t.section_end=None
                t.authority_reason=''
                t.blockers=[]
                t.state='arrived' if t.stop_index==len(t.stop_points)-1 else 'dwelling'
                t.reason=f'{arrived["label"]}: '+('terminal occupied until consist withdrawal' if t.state=='arrived' else f'dwell / give way for {arrived["dwell"]:g} simulation seconds')
                self.log('arrival',t.reason,[t.spec.id],after=t.arrivals[-1])
                if t.state!='arrived':
                    t.stop_index+=1
                changed=True
            elif t.speed<.02 and old<=t.spec.braking_mps2*dt+1e-8 and (emergency or incident_stop is not None):
                t.speed=0; t.state='incident_hold'; t.waiting+=dt
                t.reason='Incident hold: braking complete; occupied blocks and committed route remain locked'
            else:
                t.state='braking' if emergency or speed<old-.01 else 'running'
                t.reason='Controlled braking inside reserved envelope' if emergency else ('Following signal authority; '+t.authority_reason if t.authority_reason else 'Following signal authority and speed envelope')
        if incident_changed or changed or int(self.sim_time)!=self.last_plan:
            self.replan('incident transition' if incident_changed else 'movement transition' if changed else 'clock')
            self.last_plan=int(self.sim_time)
        self.quality.sample(self)
        if int(self.sim_time)!=self.last_snapshot:
            for t in self.trains.values():
                t.trace.append([round(self.sim_time,2),round(t.x,1),round(t.speed*3.6,1)])
                t.trace=t.trace[-900:]
            self.last_snapshot=int(self.sim_time)
            state=self.snapshot(include_trace=False)
            self.history.snapshot(state,self.config.retention_hours)
            score=state['quality']['score']
            if score is not None and (self.last_quality_score is None or abs(score-self.last_quality_score)>=1):
                self.log('quality',f'Movement quality {score:g}: {state["quality"]["status"]}',before=self.last_quality_score,after=state['quality'])
                self.last_quality_score=score

    def invariant_errors(self):
        owners={}; errors=[]
        for t in self.trains.values():
            if not -1e-6<=t.x<=t.total+1e-6:
                errors.append(f'{t.spec.id} off-route position')
            if t.authority is not None and t.x>t.authority+1e-6:
                errors.append(f'{t.spec.id} exceeded movement authority')
            if any(a[0] not in self.network.allowed for a in t.route):
                errors.append(f'{t.spec.id} uses excluded track')
            for r in dispatch.resources(self,t):
                if r in owners and owners[r]!=t.spec.id:
                    errors.append(f'{r} conflicting owners {owners[r]} / {t.spec.id}')
                owners[r]=t.spec.id
        return errors

    def snapshot(self,include_trace=True):
        self.state_version+=1
        trains=[]
        for t in self.trains.values():
            _,arc,offset=self.network.locate(t.route,t.x)
            eta=self.eta(t)
            physical=dispatch.resources(self,t,physical=True)
            reserved=dispatch.resources(self,t)
            block_records=[]
            for block in blocks.route_blocks(self,t):
                if block['resource'] in reserved:
                    block_records.append({key:block[key] for key in ('resource','edge','direction','index','start_m','end_m')} | {
                        'occupied':block['resource'] in physical,
                        'geometry':blocks.block_geometry(self.network,block)})
            trains.append({'id':t.spec.id,'name':t.spec.name,'spec':t.spec.model_dump(),'state':t.state,'reason':t.reason,'position':self.network.position(arc,offset),'edge':arc[0],'direction':arc[1],'distance_m':round(t.x,2),'total_m':round(t.total,2),'speed_kmh':round(t.speed*3.6,2),'importance':t.spec.importance,'delay_s':round(self.delay(t,eta),1),'destination':self.network.label(t.spec.destination),'origin':self.network.label(t.spec.origin),'predicted_arrival_s':eta,'energy_kwh':round(t.energy,3),'waiting_s':round(t.waiting,1),'dispatch_score':t.dispatch_score,'score_parts':t.score_parts,'authority_m':t.authority,'route_edges':[a[0] for a in t.route],'route':t.route,'occupied_edges':sorted(blocks.block_edges(physical)),'reserved_edges':sorted(blocks.block_edges(reserved)),'occupied_blocks':[b for b in block_records if b['occupied']],'reserved_blocks':block_records,'arrivals':t.arrivals,'actual_departure_s':t.actual_departure,'stops':t.stop_points,'hold':t.hold,'section_end_m':None if t.section_end is None or t.section_end==meets.INF else round(t.section_end,1),'reserved_tracks':t.tracks,'trace':t.trace[-300:] if include_trace else [],'ato':ato.profile(self,t,eta)})
        dispatcher={'mode':'Assumed automatic block signalling','signal_block_m':self.config.signal_block_m,
            'authority_lookahead_m':self.config.authority_lookahead_m,'assumed_signals':True,
            'opposite_direction_protection':'Next scheduled stop or dispatcher hold at a passing loop',
            'signal_failure_scope':'Entire parent map edge',
            'rules_scope':'Selected Kazakhstan signalling principles; infrastructure and station working plans are not verified'}
        return {'run_id':self.history.run,'version':self.state_version,'plan_version':self.plan_version,'sim_time':round(self.sim_time,3),'server_time':time.time(),'running':self.running,'mode':'Automatic simulation · advisory demo','dispatcher':dispatcher,'simulation_speed':self.config.simulation_speed,'trains':trains,'signals':list(self.signals.values()),'switches':list(self.switches.values()),'incidents':list(self.incidents.values()),'quality':self.quality.result(self),'alternatives':self.alternatives,'decisions':self.decisions,'deadlock':self.deadlock,'events':list(self.history.recent)[-60:] if include_trace else list(self.history.recent)[-8:],'metrics':self.metrics.copy(),'safety_errors':self.invariant_errors()}

    def load_demo(self,scenario='passing',actor='dispatcher'):
        self.reset(actor)
        if scenario=='empty':
            return
        d=self.demo
        a,b=d['origin'],d['destination']
        if scenario in {'passing','overtaking'}:
            self.add_train(TrainSpec(id='KZ-101',name='Steppe local',origin=a,destination=b,importance=3,via_siding=d['siding_edge'],give_way_s=140,max_speed_kmh=70,scheduled_arrival_s=1800),actor,False)
            # The opposing train is staged until the local has fully cleared the approach.
            self.add_train(TrainSpec(id='KZ-009',name='Altai express',origin=b if scenario=='passing' else a,destination=a if scenario=='passing' else b,departure_s=750 if scenario=='passing' else 90,importance=9,max_speed_kmh=100,scheduled_arrival_s=2200),actor,False)
        else:
            self.add_train(TrainSpec(id='KZ-201',name='Regional service',origin=a,destination=b,importance=2,scheduled_arrival_s=1600),actor,False)
            self.add_train(TrainSpec(id='KZ-010',name='Priority service',origin=a,destination=b,importance=10,scheduled_arrival_s=1500),actor,False)
        self.log('demo',f'{scenario} scenario loaded on existing siding E{d["siding_edge"]}',actor=actor,after=d)
        if scenario=='closure':
            self.add_incidents([IncidentSpec(kind='track_closure',asset_type='edge',asset_id=str(d['bypass_route'][2][0]),duration_s=180)],actor)
        self.replan('demo loaded')
