"""Bounded CP-SAT variants and a conservative priority-order fallback."""
import copy
import math
import time
import uuid
from ortools.sat.python import cp_model
from .domain import DWELL, SWITCH_TIME, HORIZON, assign_tracks, clearance, movement_profile, stops, started as was_started
from .metrics import metrics
from .validation import validate_plan


def make_move(train, leg, section, start, duration):
    return {'train_id':train['id'],'leg':leg,'section_id':section['id'],'start_s':int(start),
            'end_s':int(start+duration),'release_s':int(start+duration+clearance(train))}


def ready_time(state,train,leg):
    ready = max(train['ready_s'],math.ceil(state['sim_time_s']) + (math.ceil(state['speed']*6) if state.get('running') else 0))
    for incident in state.get('incidents',[]):
        if incident['kind']=='delay' and incident['target_id']==train['id'] and incident['leg']==leg:
            ready = max(ready,incident['end_s'])
    return math.ceil(ready)


def finish(state,movements,label,status):
    result = {'id':str(uuid.uuid4()),'label':label,'status':status,'state_version':state['state_version'],
              'sim_time_s':state['sim_time_s'],'movements':movements,'reason':'Развести встречные поезда; учесть занятость, стоянки и действующие сбои.'}
    result['violations'] = validate_plan(state,result)
    if result['violations']:
        return None
    result['station_reservations'] = assign_tracks(state['topology'],state['fleet'],movements)
    if result['station_reservations'] is None:
        return None
    result['metrics'] = metrics(state,result)
    return result


def heuristic(state):
    moves = [copy.deepcopy(m) for m in state.get('active_plan',{}).get('movements',[]) if was_started(state,m)]
    cursor = max([state['sim_time_s']]+[m['release_s'] for m in moves])+SWITCH_TIME
    for train in sorted(state['fleet'],key=lambda t:(-t['priority'],t['ready_s'],t['id'])):
        for leg,index in enumerate(train['route']):
            if any(m['train_id']==train['id'] and m['leg']==leg for m in moves):
                continue
            section = state['topology']['sections'][index]
            duration = movement_profile(train,section)['duration_s']
            cursor = max(cursor,ready_time(state,train,leg))
            for incident in sorted(state.get('incidents',[]),key=lambda x:x['start_s']):
                if incident['target_id']==section['id'] and cursor < incident['end_s'] and cursor+duration+clearance(train)>incident['start_s']:
                    cursor = incident['end_s']
            moves.append(make_move(train,leg,section,cursor,duration))
            cursor = moves[-1]['release_s']+DWELL
    return finish(state,moves,'Резервный: по приоритету','heuristic')


def shifted_seed(state):
    """Keep the existing traffic order and delay only movements not committed.

    This is a conservative feasible starting point, not a replacement for the
    independent validator: extended platform dwell can still make it invalid.
    """
    if not state.get('active_plan'):
        return None
    moves=copy.deepcopy(state['active_plan']['movements'])
    fleet={t['id']:t for t in state['fleet']}
    future=[m for m in moves if not was_started(state,m)]
    if not future:
        return finish(state,moves,'Сохранение порядка','heuristic')
    shift=max([0]+[ready_time(state,fleet[m['train_id']],m['leg'])-m['start_s'] for m in future])
    for _ in range(len(state.get('incidents',[]))+2):
        changed=False
        for m in future:
            for incident in state.get('incidents',[]):
                if incident['target_id']!=m['section_id']:
                    continue
                end=m['release_s'] if incident['kind']=='closure' else m['start_s']+1
                if m['start_s']+shift<incident['end_s'] and end+shift>incident['start_s']:
                    shift=math.ceil(incident['end_s']-m['start_s'])
                    changed=True
        if not changed:
            break
    for m in future:
        for field in ('start_s','end_s','release_s'):
            m[field]+=shift
    return finish(state,moves,'Сохранение порядка','heuristic')


def objective_value(state,plan,passenger_priority):
    base={(m['train_id'],m['leg']):m for m in state.get('baseline',plan)['movements']}
    old={(m['train_id'],m['leg']):m for m in state.get('active_plan',plan)['movements']}
    fleet={t['id']:t for t in state['fleet']}
    total=0
    by_key={(m['train_id'],m['leg']):m for m in plan['movements']}
    for key,m in by_key.items():
        train=fleet[m['train_id']]
        weight=state['settings'][f"{train['type']}_weight"]*(3 if passenger_priority and train['type']=='passenger' else 1)
        total+=max(0,m['end_s']-base[key]['end_s'])*max(1,round(weight*10))
        total+=abs(m['start_s']-old[key]['start_s'])
        if m['leg'] and m['start_s']>by_key[(m['train_id'],m['leg']-1)]['end_s']+DWELL:
            total+=60
    return total


def solve(state, passenger_priority, seconds, seed=None):
    model = cp_model.CpModel()
    topo, fleet = state['topology'],state['fleet']
    section_intervals = {s['id']:[] for s in topo['sections']}
    switch_intervals = [[] for _ in topo['stations']]
    station_intervals = [[] for _ in topo['stations']]
    old = {(m['train_id'],m['leg']):m for m in state.get('active_plan',{}).get('movements',[])}
    baseline = {(m['train_id'],m['leg']):m for m in state.get('baseline',{}).get('movements',[])}
    variables, objective = {}, []
    for train in fleet:
        route = stops(train)
        previous_end = None
        for leg,index in enumerate(train['route']):
            section = topo['sections'][index]
            key = (train['id'],leg)
            duration = movement_profile(train,section)['duration_s']
            started = key in old and was_started(state,old[key])
            lower = old[key]['start_s'] if started else ready_time(state,train,leg)
            start = model.new_int_var(lower,HORIZON-20000,f's-{key}')
            end = model.new_int_var(0,HORIZON,f'e-{key}')
            model.add(end==start+duration)
            if not state.get('active_plan') and train['id'] in ('T01','T02') and leg==0:
                model.add(start==0)
            if started:
                model.add(start==old[key]['start_s'])
            if previous_end is not None:
                model.add(start>=previous_end+DWELL)
            interval = model.new_fixed_size_interval_var(start,duration+clearance(train),f'block-{key}')
            section_intervals[section['id']].append(interval)
            switch_intervals[route[leg]].append(model.new_fixed_size_interval_var(start,SWITCH_TIME,f'exit-{key}'))
            switch_intervals[route[leg+1]].append(model.new_fixed_size_interval_var(end-SWITCH_TIME,SWITCH_TIME,f'entry-{key}'))
            station_start = 0 if previous_end is None else previous_end
            wait = model.new_int_var(0,HORIZON,f'wait-{key}')
            model.add(wait==start+SWITCH_TIME-station_start)
            station_intervals[route[leg]].append(model.new_interval_var(station_start,wait,start+SWITCH_TIME,f'platform-{key}'))
            for incident in state.get('incidents',[]):
                if incident['target_id']!=section['id'] or started:
                    continue
                before = model.new_bool_var(f'before-{key}-{incident["id"]}')
                offset = duration+clearance(train) if incident['kind']=='closure' else 1
                model.add(start+offset<=math.floor(incident['start_s'])).only_enforce_if(before)
                model.add(start>=math.ceil(incident['end_s'])).only_enforce_if(before.Not())
            expected = baseline.get(key,{}).get('end_s',train['ready_s']+(leg+1)*(duration+DWELL))
            late = model.new_int_var(0,HORIZON,f'late-{key}')
            model.add(late>=end-expected)
            settings = state['settings']
            weight = settings[f"{train['type']}_weight"]
            if passenger_priority and train['type']=='passenger':
                weight *= 3
            objective.append(late*max(1,round(weight*10)))
            if key in old:
                change = model.new_int_var(0,HORIZON,f'change-{key}')
                model.add_abs_equality(change,start-old[key]['start_s'])
                objective.append(change)
            if previous_end is not None:
                extra = model.new_bool_var(f'extra-{key}')
                model.add(start<=previous_end+DWELL).only_enforce_if(extra.Not())
                objective.append(extra*60)
            variables[key]=(start,duration,section)
            previous_end = end
        final_wait = model.new_int_var(0,HORIZON,f'final-{train["id"]}')
        model.add(final_wait==HORIZON-previous_end)
        station_intervals[route[-1]].append(model.new_interval_var(previous_end,final_wait,HORIZON,f'final-platform-{train["id"]}'))
    for intervals in list(section_intervals.values())+switch_intervals:
        model.add_no_overlap(intervals)
    for station,intervals in zip(topo['stations'],station_intervals):
        model.add_cumulative(intervals,[1]*len(intervals),station['tracks'])
    model.minimize(sum(objective))
    if seed:
        hints={(m['train_id'],m['leg']):m for m in seed['movements']}
        for key,(start,_,_) in variables.items():
            model.add_hint(start,hints[key]['start_s'])
        model.add(sum(objective)<=objective_value(state,seed,passenger_priority))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.01,seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        return None
    moves = []
    for train in fleet:
        for leg in range(5):
            start,duration,section = variables[(train['id'],leg)]
            moves.append(make_move(train,leg,section,solver.value(start),duration))
    return finish(state,moves,'Приоритет пассажирских' if passenger_priority else 'Баланс задержек',
                  'optimal' if status==cp_model.OPTIMAL else 'feasible')


def build_plans(state):
    started = time.perf_counter()
    # Warm physics once; total budget includes construction and validation.
    for train in state['fleet']:
        for section in state['topology']['sections']:
            movement_profile(train,section)
    plans = []
    seed=shifted_seed(state)
    for priority in (False,True):
        remaining = 4.3-(time.perf_counter()-started)
        if remaining<.1:
            break
        plan = solve(state,priority,min(1.3,remaining),seed)
        if seed and (not plan or objective_value(state,seed,priority)<objective_value(state,plan,priority)):
            plan=copy.deepcopy(seed)
            plan['id']=str(uuid.uuid4())
            plan['label']='Приоритет пассажирских' if priority else 'Баланс задержек'
        if plan:
            plans.append(plan)
    if not plans:
        fallback = heuristic(state)
        if fallback:
            plans.append(fallback)
    elapsed = time.perf_counter()-started
    for plan in plans:
        plan['calculation_s'] = round(elapsed,3)
        plan['within_budget'] = elapsed<=5
    return {'plans':plans,'elapsed_s':round(elapsed,3),'within_budget':elapsed<=5,
            'status':'completed' if plans else 'infeasible'}


def warm_worker(state):
    for train in state['fleet']:
        for section in state['topology']['sections']:
            movement_profile(train,section)
    return True
