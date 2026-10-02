"""Advisory speed envelope and simplified traction energy. No gradients or regen."""
import math


def energy_kwh(mass_t, distance_m, old_mps, new_mps):
    mass=mass_t*1000
    resistance=mass*9.81*.0015 + .8*((old_mps+new_mps)/2)**2
    kinetic=max(0, .5*mass*(new_mps**2-old_mps**2))
    return (resistance*max(0,distance_m)+kinetic)/(.85*3_600_000)


def following_leader(engine, train):
    """The same-direction train ahead that limits this train's authority."""
    for tid in train.blockers:
        leader=engine.trains.get(tid)
        if leader is None or leader.state in {'arrived','completed'} or not leader.launched:
            continue
        arc=engine.network.locate(leader.route,leader.x)[1]
        index=engine.network.locate(train.route,train.x)[0]
        if arc in train.route[index:]:
            return leader
    return None


def envelope(engine, train, at=None, ignore_incidents=False, now=None):
    x=train.x if at is None else at
    clock=engine.sim_time if now is None else now
    net=engine.network
    if train.authority is None:
        return 0.0
    stop=min(train.authority,train.next_stop['distance'])
    limit=train.spec.max_speed_kmh/3.6
    braking=train.spec.braking_mps2
    # One maximum integration step at top speed, so a lower limit is reached
    # before the head crosses into its section rather than during the step.
    margin=limit*.2
    lengths=net.lengths(train.route)
    rear=x-train.spec.length_m
    for i,arc in enumerate(train.route):
        # A limit applies until the rear of the consist has left the section.
        if lengths[i+1] < rear-1e-6:
            continue
        gap=max(0,lengths[i]-x-margin) if lengths[i]>x else 0
        if 2*braking*gap>=limit**2:
            # Farther sections cannot bind: their braking curves are higher.
            break
        edge_limit=net.speed_limit(arc[0])/3.6
        if not ignore_incidents:
            edge_limit=min(edge_limit,engine.restriction(arc[0])/3.6)
        limit=min(limit, math.sqrt(max(0,edge_limit**2 + 2*braking*gap)))
    if not ignore_incidents:
        incident_stop, emergency=engine.incident_stop(train)
        if incident_stop is not None:
            stop=min(stop,incident_stop)
        if emergency:
            return 0.0
    remaining=max(0,stop-x)
    # One maximum integration step of margin starts braking before the ideal
    # curve; the resulting resting gap stays within the 5 cm stop tolerance.
    limit=min(limit,max(0,math.sqrt(2*braking*remaining)-braking*.2))
    leader=following_leader(engine,train)
    if leader is not None and leader.speed>1 and remaining<limit**2/(2*braking)+2*engine.config.signal_block_m:
        # Close behind a moving leader: match its speed instead of running up
        # to the next red signal and braking (stop-and-go wastes traction).
        limit=min(limit,leader.speed+.5)
    target=train.next_stop.get('scheduled')
    # A train that others are waiting for runs at line speed instead of
    # spending timetable slack while it delays them.
    if target and target>clock+5 and remaining>100 and train.spec.id not in engine.blocking:
        # Schedule pacing uses the actual next stop, not a temporary red signal.
        efficient=max(0,train.next_stop['distance']-x)/max(1,target-clock-5)
        limit=min(limit,max(5/3.6,efficient))
    return limit


def profile(engine, train, eta=...):
    remaining=max(0,train.total-train.x)
    step=max(10,min(200,remaining/30))
    points=[]
    # Each future point is evaluated at its estimated passing time; using the
    # present clock would show a false deceleration toward a timetabled stop.
    clock=engine.sim_time
    previous=None
    for i in range(31):
        x=min(train.total,train.x+i*step)
        if previous is not None:
            clock+=(x-previous[0])/max(1,previous[1])
        speed=envelope(engine,train,x,now=clock)
        points.append({'distance_m':round(x-train.x,1),'recommended_kmh':round(speed*3.6,1)})
        previous=(x,speed)
        if x>=train.total:
            break
    return {'train_id':train.spec.id,'actual_kmh':round(train.speed*3.6,1),'recommended_kmh':round(envelope(engine,train)*3.6,1),'predicted_arrival_s':engine.eta(train) if eta is ... else eta,'energy_kwh':round(train.energy,3),'model':'Simplified traction kWh: rolling + aerodynamic resistance + positive kinetic energy, 85% efficiency; no grade or regeneration.','points':points}
