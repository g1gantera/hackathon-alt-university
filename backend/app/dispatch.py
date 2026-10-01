"""Explain validated reservations. This module never grants a movement authority."""
from .domain import DWELL, SWITCH_TIME, stops, started
from .validation import validate_plan


def priority_weight(state, train, passenger_priority=False):
    """Soft lateness penalty; resource constraints always take precedence."""
    weight=train['priority']*state['settings'][f"{train['type']}_weight"]
    if passenger_priority and train['type']=='passenger':
        weight*=3
    return max(1,round(weight*10))


def dispatch_report(state, plan):
    errors=validate_plan(state,plan)
    fleet={t['id']:t for t in state['fleet']}
    conflicts=[]
    for error in errors:
        item=dict(error)
        if item['code']=='occupancy':
            a,b=fleet[item['train_id']],fleet[item['other_train_id']]
            item['kind']='opposing' if a['direction']!=b['direction'] else 'following'
        else:
            item['kind']=item['code']
        conflicts.append(item)
    report={'plan_id':plan['id'],'epoch':state['epoch'],'state_version':state['state_version'],
            'sim_time_s':state['sim_time_s'],'valid':not errors,'conflicts':conflicts,
            'policy':plan.get('policy','baseline'),'decisions':[],'next_decisions':[],'section_order':[]}
    # Malformed plans are reported without attempting to interpret their timings.
    if any(e['code'] in ('route','time','physics','dwell','clearance','horizon') for e in errors):
        return report
    moves=plan['movements']
    now=state['sim_time_s']
    topo=state['topology']
    for section in topo['sections']:
        reservations=sorted((m for m in moves if m['section_id']==section['id']),key=lambda m:(m['start_s'],m['train_id']))
        report['section_order'].append({'section_id':section['id'],'reservations':reservations})
    for train in state['fleet']:
        legs=sorted((m for m in moves if m['train_id']==train['id']),key=lambda m:m['leg'])
        route=stops(train)
        decisions=[]
        for i,m in enumerate(legs):
            ready=max(train['ready_s'],legs[i-1]['end_s']+DWELL if i else train['ready_s'])
            holds=[x for x in state.get('incidents',[]) if x['kind']=='delay' and x['target_id']==train['id'] and x['leg']==i]
            ready=max([ready]+[x['end_s'] for x in holds])
            predecessors=[]
            for other in moves:
                if other['train_id']==train['id']:
                    continue
                if other['section_id']==m['section_id'] and ready<other['release_s']<=m['start_s']:
                    predecessors.append({'train_id':other['train_id'],'resource':'section','release_s':other['release_s']})
                other_route=stops(fleet[other['train_id']])
                for station,end in ((other_route[other['leg']],other['start_s']+SWITCH_TIME),
                                    (other_route[other['leg']+1],other['end_s'])):
                    if station==route[i] and ready<end<=m['start_s']:
                        predecessors.append({'train_id':other['train_id'],'resource':'switch','release_s':end})
            predecessors.sort(key=lambda p:(p['release_s'],p['train_id'],p['resource']))
            committed=started(state,m)
            action=('passing' if now<m['end_s'] else 'completed') if committed else (
                'held' if state.get('awaiting_plan') or errors else 'wait' if now<m['start_s'] else 'ready')
            decisions.append({**m,'station_id':topo['stations'][route[i]]['id'],
                'priority':train['priority'],'lateness_weight':priority_weight(state,train,report['policy']=='passenger_priority')/10,
                'ready_s':ready,'wait_s':max(0,m['start_s']-ready),'remaining_wait_s':max(0,m['start_s']-now),
                'action':action,'committed':committed,'predecessors':predecessors,
                'reason':'committed' if committed else 'hold' if action=='held' else 'resource_order' if predecessors
                         else 'ready_time' if m['start_s']==ready else 'coordinated_schedule'})
        report['decisions'].extend(decisions)
        report['next_decisions'].append(next((d for d in decisions if d['action']!='completed'),decisions[-1]))
    return report
