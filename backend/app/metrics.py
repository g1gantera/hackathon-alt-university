from .domain import movement_profile, started
from .advisory import sample
from .validation import validate_plan
from .capacity import capacity_metrics
from .quality_config import DEFAULT_SETTINGS, quality_weights, combine_scores, quality_assessment
from collections import Counter
import hashlib
import json
import numpy as np

def metrics(state, plan, forecast=True, violations=None):
    """Version 4: configurable aggregation, weights, penalties and status thresholds.

    Live delay includes overdue, unfinished arrivals. Energy is compared with the
    baseline at equivalent distance, so normal movement alone is not a penalty.
    Capacity measures occupancy and delivered train-distance, not train speed.
    """
    baseline = {(m['train_id'],m['leg']):m for m in state.get('baseline',plan)['movements']}
    fleet = {t['id']:t for t in state['fleet']}
    sections = {s['id']:s for s in state['topology']['sections']}
    settings = {**DEFAULT_SETTINGS, **state.get('settings',{})}
    now = state['sim_time_s']
    departures={'evaluated':0,'on_time':0,'late':0,'early':0,'deviation_s':0,'delay_s':0}
    terminals={'evaluated':0,'on_time':0,'late':0,'early':0,'deviation_s':0,'delay_s':0}
    delays, weighted, passenger, energy, reference_energy, on_time, arrivals, trips = [],0,0,0,0,0,0,0
    for m in plan['movements']:
        train = fleet[m['train_id']]
        base = baseline[(m['train_id'],m['leg'])]
        arrived = started(state,m) and m['end_s']<=now
        if forecast or started(state,m) or now>base['start_s']:
            departure=m['start_s'] if forecast or started(state,m) else now
            deviation=departure-base['start_s']
            departures['evaluated']+=1
            departures['deviation_s']+=abs(deviation)
            departures['delay_s']+=max(0,deviation)
            departures['on_time']+=abs(deviation)<=settings['arrival_tolerance_s']
            departures['late']+=deviation>settings['arrival_tolerance_s']
            departures['early']+=deviation < -settings['arrival_tolerance_s']
        if m['leg']==len(train['route'])-1 and (forecast or arrived or now>base['end_s']+settings['arrival_tolerance_s']):
            deviation=(m['end_s'] if forecast or arrived else now)-base['end_s']
            terminals['evaluated']+=1
            terminals['deviation_s']+=abs(deviation)
            terminals['delay_s']+=max(0,deviation)
            terminals['on_time']+=abs(deviation)<=settings['arrival_tolerance_s']
            terminals['late']+=deviation>settings['arrival_tolerance_s']
            terminals['early']+=deviation < -settings['arrival_tolerance_s']
        if forecast or arrived or now>=base['end_s']:
            arrival = m['end_s'] if forecast or arrived else now
            delay = max(0,arrival-base['end_s'])
            delays.append(delay)
            weighted += delay*settings[f"{train['type']}_weight"]
            passenger += delay if train['type']=='passenger' else 0
            if forecast or arrived or delay>settings['arrival_tolerance_s']:
                on_time += delay<=settings['arrival_tolerance_s']
                arrivals += 1
            trips += m['leg']==len(train['route'])-1 and (forecast or arrived)
        p = movement_profile(train,sections[m['section_id']],m['end_s']-m['start_s'])
        reference = movement_profile(train,sections[base['section_id']],base['end_s']-base['start_s'])
        if forecast:
            energy += p['energy_kwh']
            reference_energy += reference['energy_kwh']
        elif started(state,m) and now>m['start_s']:
            distance,_,used = sample(p,min(p['duration_s'],now-m['start_s']))
            energy += used
            reference_energy += used if p['duration_s']==reference['duration_s'] else float(np.interp(
                distance,[point[1] for point in reference['points']],[point[3] for point in reference['points']]))
    total = sum(delays)
    blocked = sorted({i['target_id'] for i in state.get('incidents',[]) if i['kind'] in ('closure','signal')
                      and i['target_id'] in sections and i['start_s']<=now<i['end_s'] and 'resolved_s' not in i})
    errors=validate_plan(state,plan) if violations is None else violations
    conflicts = len(errors)
    capacity=capacity_metrics(state,plan,forecast)
    terminals.update(pending=len(fleet)-terminals['evaluated'],
                     on_time_pct=round(100*terminals['on_time']/terminals['evaluated'],2) if terminals['evaluated'] else None)
    departures['on_time_pct']=round(100*departures['on_time']/departures['evaluated'],2) if departures['evaluated'] else None
    scores = {
        'schedule':max(0,1-departures['deviation_s']/settings['delay_norm_s']),
        'energy':max(0,1-max(0,energy-reference_energy)/settings['energy_norm_kwh']),
        'capacity':capacity['score']/100,
        'conflicts':1/(1+settings['conflict_penalty']*conflicts),
        'arrival_accuracy':terminals['on_time']/terminals['evaluated'] if terminals['evaluated'] else 1,
    }
    weights=quality_weights(settings)
    quality,losses=combine_scores(scores,weights,settings['quality_formula'])
    quality=round(quality,1)
    observed={'schedule':departures['evaluated']>0,'capacity':True,'energy':energy>1e-6,
              'conflicts':True,'arrival_accuracy':terminals['evaluated']>0}
    components={key:{'score':round(value*100,2),'weight':round(weights[key],6),
                     'loss_points':round(losses[key],3),'observed':observed[key]} for key,value in scores.items()}
    formula={'version':4,'aggregation':settings['quality_formula'],'weights':weights,'delay_norm_s':settings['delay_norm_s'],
             'energy_norm_kwh':settings['energy_norm_kwh'],'arrival_tolerance_s':settings['arrival_tolerance_s'],
             'capacity_window_s':900,'conflict_penalty':settings['conflict_penalty'],
             'thresholds':{'normal':settings['quality_threshold_normal'],'attention':settings['quality_threshold_attention']}}
    signature=hashlib.sha256(json.dumps(formula,sort_keys=True).encode()).hexdigest()[:16]
    counts=Counter(e['code'] for e in errors)
    scheduled = sum(m['leg']==len(fleet[m['train_id']]['route'])-1 and (forecast or m['end_s']<=now) for m in baseline.values())
    return {'total_delay_s':total,'weighted_delay_s':weighted,'max_delay_s':max(delays,default=0),
            'passenger_delay_s':passenger,'energy_kwh':round(energy,2),'index':round(quality,1),
            'on_time_pct':round(on_time/arrivals*100,1) if arrivals else None,
            'completed_trips':trips,'scheduled_trips':scheduled,'completion_pct':round(trips/scheduled*100,1) if scheduled else None,
            'conflicts':conflicts, 'forecast':forecast,'quality_version':4,'quality_signature':signature,
            'formula':formula,'departures':departures,'terminal_arrivals':terminals,'capacity':capacity,
            'energy':{'used_kwh':round(energy,2),'reference_kwh':round(reference_energy,2),
                      'saved_kwh':round(reference_energy-energy,2),
                      'saved_pct':round(100*(reference_energy-energy)/reference_energy,2) if reference_energy>1e-6 else None},
            'conflict_summary':{'total':conflicts,'by_code':dict(counts),
                                'resource_conflicts':sum(counts[k] for k in ('occupancy','switch','capacity')),
                                'restriction_violations':sum(counts[k] for k in ('closed','signal','delay')),
                                'other_violations':sum(counts[k] for k in counts if k not in ('occupancy','switch','capacity','closed','signal','delay'))},
            'forecast_valid':not errors and not (state.get('awaiting_plan') and plan['id']==state.get('active_plan',{}).get('id')) if forecast else None,
            'assessment':quality_assessment(quality,settings),
            'blocked_sections':blocked,'available_sections':len(sections)-len(blocked),'total_sections':len(sections),
            'reference_energy_kwh':round(reference_energy,2),'evaluated_arrivals':arrivals,
            'components':components,
            'drivers':[{'component':key,'loss_points':item['loss_points']} for key,item in sorted(components.items(),key=lambda pair:-pair[1]['loss_points']) if item['loss_points']>=.05]}
