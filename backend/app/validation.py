"""Independent checks applied to heuristic, solver and plan-apply results."""
from collections import defaultdict
from .domain import DWELL, SWITCH_TIME, HORIZON, clearance, movement_profile, station_intervals, stops, started


def validate_plan(state, plan):
    errors = []
    def fail(code, message, **context):
        errors.append({'code':code,'message':message,**context})
    topo, fleet = state['topology'], state['fleet']
    moves = plan.get('movements',[])
    indexed = {(m['train_id'],m['leg']):m for m in moves}
    if len(indexed)!=len(moves) or len(moves)!=len(fleet)*5:
        fail('route','Missing or duplicate movements')
        return errors
    resources = defaultdict(list)
    for train in fleet:
        previous = None
        route = stops(train)
        for leg, section_index in enumerate(train['route']):
            m = indexed.get((train['id'],leg))
            if not m:
                fail('route','Train route incomplete',train_id=train['id'])
                continue
            section = topo['sections'][section_index]
            if m['section_id'] != section['id']:
                fail('route','Wrong section',train_id=train['id'])
            minimum = movement_profile(train,section)['minimum_time_s']
            if m['end_s']-m['start_s'] < minimum or m['start_s'] < 0:
                fail('physics','Unreachable running time',train_id=train['id'])
            if m['release_s'] > HORIZON:
                fail('horizon','Movement exceeds the scenario horizon',train_id=train['id'])
            if m['release_s'] < m['end_s']+clearance(train):
                fail('clearance','Tail clearance missing',train_id=train['id'])
            if m['start_s'] < (previous['end_s']+DWELL if previous else train['ready_s']):
                fail('dwell','Minimum dwell or ready time violated',train_id=train['id'])
            for incident in state.get('incidents',[]):
                if incident['kind']=='delay' and incident['target_id']==train['id'] and m['leg']==incident['leg']:
                    if m['start_s'] < incident['end_s']:
                        fail('delay','Station hold violated',train_id=train['id'])
                if incident['target_id']==section['id']:
                    if incident['start_s'] <= m['start_s'] < incident['end_s']:
                        fail('signal' if incident['kind']=='signal' else 'closed','Entry forbidden',train_id=train['id'],section_id=section['id'])
                    # Already-entered trains are allowed to clear a newly closed resource.
                    if incident['kind']=='closure' and m['start_s'] >= incident['created_s'] and m['start_s'] < incident['end_s'] and m['release_s'] > incident['start_s']:
                        fail('closed','Movement overlaps closure',train_id=train['id'],section_id=section['id'])
            resources[section['id']].append((m['start_s'],m['release_s'],train['id']))
            resources[f'switch-{route[leg]}'].append((m['start_s'],m['start_s']+SWITCH_TIME,train['id']))
            resources[f'switch-{route[leg+1]}'].append((m['end_s']-SWITCH_TIME,m['end_s'],train['id']))
            previous = m
    for resource, items in resources.items():
        items.sort()
        for i,(start,end,train_id) in enumerate(items):
            for other_start,other_end,other_id in items[i+1:]:
                if other_start>=end:
                    break
                fail('switch' if resource.startswith('switch') else 'occupancy','Conflicting reservations',resource=resource,train_id=train_id,other_train_id=other_id)
    for si,station in enumerate(topo['stations']):
        events = []
        for item in station_intervals(topo,fleet,moves):
            if item['station_index']==si:
                events.extend([(item['start_s'],1),(item['end_s'],-1)])
        count = 0
        for _,delta in sorted(events):
            count += delta
            if count>station['tracks']:
                fail('capacity','Station capacity exceeded',station_id=station['id'])
                break
    now = state['sim_time_s']
    old_index = {(m['train_id'],m['leg']):m for m in state.get('active_plan',{}).get('movements',[])}
    for key,m in indexed.items():
        old = old_index.get(key)
        if old and started(state,old):
            if m != old:
                fail('past','Started movement changed',train_id=key[0])
        elif m['start_s'] < now:
            fail('past','New departure lies in the past',train_id=key[0])
    return errors
