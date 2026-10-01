"""Stateless teaching sandbox using the real stage-3 solver and validator."""
import copy
import time

from .advisory import sample
from .dispatch import dispatch_report
from .domain import DWELL, clearance, movement_profile, topology, trains
from .metrics import DEFAULT_SETTINGS
from .planning import heuristic, solve

SCENARIOS=('opposing','following','fleet')


def demo_state(scenario, priorities=None):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown scenario')
    fleet=trains()
    fleet=fleet if scenario=='fleet' else [fleet[i] for i in ((0,3) if scenario=='opposing' else (0,2))]
    for index,train in enumerate(fleet):
        # Independent IDs also avoid the initial main-scenario departure anchors.
        train.update(id=f'D{index+1:02}',ready_s=0)
    priorities=priorities or {}
    if set(priorities)-{t['id'] for t in fleet}:
        raise ValueError('Unknown train priority')
    for train in fleet:
        train['priority']=priorities.get(train['id'],train['priority'])
    state={'topology':topology(),'fleet':fleet,'sim_time_s':0,'state_version':0,'epoch':'stage3-sandbox',
           'speed':1,'running':False,'incidents':[],'settings':copy.deepcopy(DEFAULT_SETTINGS),'committed':[]}
    requested=[]
    for train in fleet:
        start=train['ready_s']
        for leg,index in enumerate(train['route']):
            section=state['topology']['sections'][index]
            end=start+movement_profile(train,section)['duration_s']
            requested.append({'train_id':train['id'],'leg':leg,'section_id':section['id'],
                              'start_s':start,'end_s':end,'release_s':end+clearance(train)})
            start=end+DWELL
    state['baseline']={'id':'uncoordinated-requests','policy':'requests','movements':requested}
    return state


def scenario_data(state):
    topo=state['topology']
    return {'trains':state['fleet'],'length_m':topo['length_m'],
            'stations':[{k:s[k] for k in ('id','name','position_m')} for s in topo['stations']],
            'sections':[{k:s[k] for k in ('id','from_station','to_station','length_m')} for s in topo['sections']],
            'before':dispatch_report(state,state['baseline'])}


def solve_demo(scenario, priorities, policy):
    began=time.perf_counter()
    state=demo_state(scenario,priorities)
    plan=solve(state,policy=='passenger_priority',2.5)
    if plan is None:
        plan=heuristic(state)
    if plan is None:
        return {**scenario_data(state),'status':'no_valid_plan','after':None}
    report=dispatch_report(state,plan)
    if not report['valid']:
        raise RuntimeError('Solver returned an invalid demonstration plan')
    profiles={}
    for movement in plan['movements']:
        train=next(t for t in state['fleet'] if t['id']==movement['train_id'])
        section=next(s for s in state['topology']['sections'] if s['id']==movement['section_id'])
        duration=movement['end_s']-movement['start_s']
        profile=movement_profile(train,section,duration)
        profiles[f'{train["id"]}:{movement["leg"]}']=[[t,round(sample(profile,t)[0],2)] for t in [*range(0,duration,60),duration]]
    return {**scenario_data(state),'status':'completed','after':report,'profiles':profiles,
            'solver_status':plan['status'],'requested_policy':policy,'calculation_s':round(time.perf_counter()-began,3)}
