"""Schedule-following advice and bounded, independently validated eco proposals."""
import copy
import time
import uuid

from .advisory import sample
from .domain import DWELL, assign_tracks, movement_profile, started, stops
from .metrics import metrics
from .validation import validate_plan

LOOKAHEAD_S = 10
MAX_ECO_EXTENSION_S = 900


def train_legs(plan, train_id):
    return sorted((m for m in plan['movements'] if m['train_id']==train_id),key=lambda m:m['leg'])


def live_advice(state, train, telemetry, plan_valid=True):
    """Small enough for every WebSocket snapshot; detailed samples use the REST API."""
    legs=train_legs(state['active_plan'],train['id'])
    sections={s['id']:s for s in state['topology']['sections']}
    now=state['sim_time_s']
    held=state.get('awaiting_plan',False) or not plan_valid
    move=next((m for m in legs if not started(state,m) or m['end_s']>now),None)
    total=fastest=remaining=0.0
    for m in legs:
        p=movement_profile(train,sections[m['section_id']],m['end_s']-m['start_s'])
        total+=p['energy_kwh']
        fastest+=movement_profile(train,sections[m['section_id']])['energy_kwh']
        used=sample(p,min(p['duration_s'],max(0,now-m['start_s'])))[2] if started(state,m) else 0
        remaining+=max(0,p['energy_kwh']-used)
    advice={'plan_id':state['active_plan']['id'],'sim_time_s':now,
            'current_speed_mps':telemetry['speed_mps'],'recommended_speed_mps':0.0,'lookahead_speed_mps':0.0,
            'lookahead_s':LOOKAHEAD_S,'limit_mps':0.0,'phase':'completed','reason':'route_completed',
            'section_id':None,'next_station_id':None,'departure_s':None,'next_arrival_s':None,
            'final_arrival_s':legs[-1]['end_s'] if not held or telemetry['status']=='completed' else None,
            'energy_remaining_kwh':None if held else round(remaining,2),
            'energy_plan_kwh':None if held else round(total,2),
            'energy_saving_kwh':None if held else round(max(0,fastest-total),2),
            'energy_saving_pct':None if held else round(100*max(0,fastest-total)/max(fastest,1e-9),2),
            'forecast_available':not held,'source':'simulation'}
    if move is None:
        advice.update(energy_remaining_kwh=0.0)
        return advice
    section=sections[move['section_id']]
    p=movement_profile(train,section,move['end_s']-move['start_s'])
    advice.update(section_id=section['id'],limit_mps=p['limit_mps'],
                  next_station_id=state['topology']['stations'][stops(train)[move['leg']+1]]['id'])
    if started(state,move):
        elapsed=max(0,now-move['start_s'])
        current=sample(p,elapsed)[1]
        upcoming=sample(p,min(p['duration_s'],elapsed+LOOKAHEAD_S))[1]
        phase='braking' if upcoming<current-.01 else 'accelerating' if upcoming>current+.01 else 'cruising'
        advice.update(recommended_speed_mps=current,lookahead_speed_mps=upcoming,phase=phase,
                      reason='clear_committed_section' if held else 'follow_active_profile',
                      departure_s=move['start_s'],next_arrival_s=move['end_s'])
        if move==legs[-1]:
            advice['final_arrival_s']=move['end_s']
    else:
        advice.update(phase='held' if held else 'waiting',reason='awaiting_valid_plan' if held else 'scheduled_departure',
                      departure_s=None if held else move['start_s'],next_arrival_s=None if held else move['end_s'])
    return advice


def train_profile(state, train, plan=None):
    plan=plan or state['active_plan']
    valid=not validate_plan(state,plan)
    provisional=(plan['id']==state['active_plan']['id'] and state.get('awaiting_plan',False)) or not valid
    points=[]
    energy=position=0.0
    legs=train_legs(plan,train['id'])
    for m in legs:
        section=next(s for s in state['topology']['sections'] if s['id']==m['section_id'])
        p=movement_profile(train,section,m['end_s']-m['start_s'])
        if not provisional or started(state,m):
            # Explicit zero-speed endpoints connect station dwell without invented motion.
            if not points and m['start_s']>0:
                points.append([0,position,0,p['limit_mps'],energy])
            points.extend([[m['start_s']+t,position+x,v,p['limit_mps'],energy+e] for t,x,v,e in p['points']])
        energy+=p['energy_kwh']
        position+=section['length_m']
    return {'train_id':train['id'],'plan_id':plan['id'],'epoch':state['epoch'],
            'constraint_version':state['constraint_version'],'points':points,
            'energy_kwh':None if provisional else energy,'arrival_s':None if provisional else legs[-1]['end_s'],
            'reachable':not provisional,'provisional':provisional,
            'assumptions':'Level track; no regeneration; synthetic traction parameters. Forecast, not measured savings.'}


def eco_plan(state, train_id):
    """Use existing intermediate dwell slack; never change a departure or terminal ETA.

    A small descending grid is deliberate: switch conflicts are non-monotone,
    so a binary search for feasibility would be incorrect. No optimality claim.
    """
    began=time.perf_counter()
    train=next(t for t in state['fleet'] if t['id']==train_id)
    original=state['active_plan']
    if state.get('awaiting_plan') or validate_plan(state,original):
        return {'plan':None,'reason':'awaiting_valid_plan'}
    candidate=copy.deepcopy(original)
    legs=train_legs(candidate,train_id)
    changes=[]
    sections={s['id']:s for s in state['topology']['sections']}
    for i,move in enumerate(legs[:-1]):
        if started(state,move):
            continue
        old=copy.deepcopy(move)
        upper=min(MAX_ECO_EXTENSION_S,legs[i+1]['start_s']-DWELL-old['end_s'])
        for other in candidate['movements']:
            if other['section_id']==move['section_id'] and other['start_s']>=old['release_s']:
                upper=min(upper,other['start_s']-old['release_s'])
        for incident in state.get('incidents',[]):
            if incident['kind']=='closure' and incident['target_id']==move['section_id'] and incident['start_s']>=old['release_s']:
                upper=min(upper,incident['start_s']-old['release_s'])
        if upper<=0:
            continue
        section=sections[move['section_id']]
        before=movement_profile(train,section,old['end_s']-old['start_s'])
        extensions=sorted({int(upper*f) for f in (1,.75,.5,.25)}|{min(int(upper),x) for x in (60,30,10,1)},reverse=True)
        for extra in extensions:
            if extra<=0:
                continue
            move.update(end_s=old['end_s']+extra,release_s=old['release_s']+extra)
            if validate_plan(state,candidate):
                continue
            after=movement_profile(train,section,move['end_s']-move['start_s'])
            saving=before['energy_kwh']-after['energy_kwh']
            if not after['reachable'] or saving<=.01:
                continue
            changes.append({'leg':move['leg'],'section_id':move['section_id'],
                            'before_arrival_s':old['end_s'],'after_arrival_s':move['end_s'],
                            'extra_running_s':extra,'saving_kwh':round(saving,2),
                            'before_cruise_mps':max(p[2] for p in before['points']),
                            'after_cruise_mps':max(p[2] for p in after['points'])})
            break
        else:
            move.update(old)
    if not changes:
        return {'plan':None,'reason':'no_safe_slack'}
    errors=validate_plan(state,candidate)
    tracks=assign_tracks(state['topology'],state['fleet'],candidate['movements'])
    if errors or tracks is None:
        return {'plan':None,'reason':'no_safe_slack'}
    before_profile=train_profile(state,train,original)
    candidate.update(id=str(uuid.uuid4()),label=f"Экономичный ход · № {train['number']}",status='heuristic',
                     policy='eco',source_plan_id=original['id'],epoch=state['epoch'],
                     constraint_version=state['constraint_version'],state_version=state['state_version'],
                     sim_time_s=state['sim_time_s'],violations=[],station_reservations=tracks,
                     reason='Снизить скорость за счёт стоянки; сохранить отправления и конечное прибытие.')
    after_profile=train_profile(state,train,candidate)
    saved=before_profile['energy_kwh']-after_profile['energy_kwh']
    candidate['eco']={'train_id':train_id,'changes':changes,'saving_kwh':round(saved,2),
                      'saving_pct':round(saved/before_profile['energy_kwh']*100,2),
                      'before_energy_kwh':round(before_profile['energy_kwh'],2),
                      'after_energy_kwh':round(after_profile['energy_kwh'],2),
                      'final_arrival_s':legs[-1]['end_s']}
    candidate['metrics']=metrics(state,candidate)
    candidate['calculation_s']=round(time.perf_counter()-began,3)
    candidate['within_budget']=candidate['calculation_s']<=5
    return {'plan':candidate,'profile':after_profile,'reason':'proposal_ready'}
