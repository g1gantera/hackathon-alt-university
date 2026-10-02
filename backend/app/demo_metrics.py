from .domain import movement_profile, started
from .demo_advisory import sample

DEFAULT_SETTINGS = {'passenger_weight':3.0,'freight_weight':1.0,'delay_weight':0.7,'energy_weight':0.3,
                    'delay_norm_s':7200.0,'energy_norm_kwh':100000.0,'arrival_tolerance_s':300.0}


def metrics(state, plan, forecast=True):
    baseline = {(m['train_id'],m['leg']):m for m in state.get('baseline',plan)['movements']}
    fleet = {t['id']:t for t in state['fleet']}
    sections = {s['id']:s for s in state['topology']['sections']}
    settings = state.get('settings',DEFAULT_SETTINGS)
    now = state['sim_time_s']
    delays, weighted, passenger, energy, on_time, arrivals, trips = [],0,0,0,0,0,0
    for m in plan['movements']:
        train = fleet[m['train_id']]
        if forecast or (started(state,m) and m['end_s']<=now):
            delay = max(0,m['end_s']-baseline[(m['train_id'],m['leg'])]['end_s'])
            delays.append(delay)
            weighted += delay*settings[f"{train['type']}_weight"]
            passenger += delay if train['type']=='passenger' else 0
            on_time += delay<=settings['arrival_tolerance_s']
            arrivals += 1
            trips += m['leg']==4
        p = movement_profile(train,sections[m['section_id']],m['end_s']-m['start_s'])
        if forecast:
            energy += p['energy_kwh']
        elif started(state,m) and now>m['start_s']:
            energy += sample(p,min(p['duration_s'],now-m['start_s']))[2]
    total = sum(delays)
    dw,ew = settings['delay_weight'],settings['energy_weight']
    loss = (dw*min(1,total/settings['delay_norm_s'])+ew*min(1,energy/settings['energy_norm_kwh']))/(dw+ew)
    scheduled = sum(m['leg']==4 and (forecast or m['end_s']<=now) for m in baseline.values())
    return {'total_delay_s':total,'weighted_delay_s':weighted,'max_delay_s':max(delays,default=0),
            'passenger_delay_s':passenger,'energy_kwh':round(energy,2),'index':round(100*(1-loss),1),
            'on_time_pct':round(on_time/arrivals*100,1) if arrivals else None,
            'completed_trips':trips,'scheduled_trips':scheduled,'completion_pct':round(trips/scheduled*100,1) if scheduled else None,
            'conflicts':len(plan.get('violations',[])), 'forecast':forecast}
