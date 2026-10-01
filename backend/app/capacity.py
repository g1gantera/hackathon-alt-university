"""Measured section use and delivered train-distance in a fixed comparison window."""
from .advisory import sample
from .domain import movement_profile, started

WINDOW_S = 900


def union_seconds(intervals, start, end):
    clipped=sorted((max(start,a),min(end,b)) for a,b in intervals if max(start,a)<min(end,b))
    total=0.0
    cursor=start
    for a,b in clipped:
        total+=max(0,b-max(a,cursor))
        cursor=max(cursor,b)
    return total


def capacity_metrics(state,plan,forecast=False):
    sections=state['topology']['sections']
    fleet={t['id']:t for t in state['fleet']}
    baseline=state.get('baseline',plan)
    now=state['sim_time_s']
    end=max((m['release_s'] for m in baseline['movements']),default=0) if forecast else now
    start=0 if forecast else max(0,end-WINDOW_S)
    window=max(0,end-start)
    rows=[]
    def distance(m,section):
        if m['start_s']>=end or m['end_s']<=start:
            return 0.0
        p=movement_profile(fleet[m['train_id']],section,m['end_s']-m['start_s'])
        def at(t):
            if t<=m['start_s']:return 0.0
            if t>=m['end_s']:return section['length_m']
            return sample(p,t-m['start_s'])[0]
        return max(0,at(end)-at(start))
    for section in sections:
        moves=[m for m in plan['movements'] if m['section_id']==section['id'] and (forecast or started(state,m))]
        base_moves=[m for m in baseline['movements'] if m['section_id']==section['id']]
        incidents=[i for i in state.get('incidents',[]) if i['kind'] in ('closure','signal') and i['target_id']==section['id']]
        occupied=union_seconds([(m['start_s'],m['release_s']) for m in moves],start,end)
        blocked=union_seconds([(i['start_s'],i['end_s']) for i in incidents],start,end)
        rows.append({'section_id':section['id'],
                     'blocked_now':any(i['start_s']<=now<i['end_s'] and 'resolved_s' not in i for i in incidents),
                     'occupied_s':round(occupied,3),'entry_blocked_s':round(blocked,3),
                     'utilization_pct':round(100*occupied/window,2) if window else None,
                     'distance_m':sum(distance(m,section) for m in moves),
                     'reference_distance_m':sum(distance(m,section) for m in base_moves)})
    delivered=sum(r['distance_m'] for r in rows)
    reference=sum(r['reference_distance_m'] for r in rows)
    availability=1-sum(r['blocked_now'] for r in rows)/max(1,len(rows))
    delivery=min(1,delivered/reference) if reference>1e-6 else 1.0
    for row in rows:
        row['distance_m']=round(row['distance_m'],3)
        row['reference_distance_m']=round(row['reference_distance_m'],3)
    return {'window_start_s':start,'window_end_s':end,'window_s':window,
            'basis':'baseline_horizon' if forecast else 'rolling_15_minutes',
            'availability_pct':round(100*availability,2),
            'utilization_pct':round(100*sum(r['occupied_s'] for r in rows)/(window*max(1,len(rows))),2) if window else None,
            'entry_blocked_pct':round(100*sum(r['entry_blocked_s'] for r in rows)/(window*max(1,len(rows))),2) if window else None,
            'distance_m':round(delivered,3),'reference_distance_m':round(reference,3),
            'delivery_pct':round(100*delivered/reference,2) if reference>1e-6 else None,
            'score':100*availability*delivery,'sections':rows}
