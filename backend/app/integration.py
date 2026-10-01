"""Bridge the logic branch's typed planner to the live dispatcher's API.

The legacy demo remains selectable with DISPATCH_ENGINE=demo. Native plans,
profiles and constraints are retained; UI projections never drive validation.
"""
import copy
import json
import math
import uuid

import numpy as np

from .domain import ROOT
from .demo_metrics import DEFAULT_SETTINGS
from . import demo_planning, demo_validation
from .metrics.quality import MetricConfig, calculate_metrics, profile_plan
from .planning.baseline import build_baseline
from .planning.common import clearance_s
from .planning.service import plan_alternatives
from .schemas import Block, EntrySpeedLimit, Plan, Scenario
from .scenarios import corridor_scenario, load_infrastructure, incident_scenario, INCIDENTS
from .validation.plan import validate_plan as native_validate


def config_for(state):
    settings = state['settings']
    total = settings['delay_weight'] + settings['energy_weight']
    return MetricConfig(weights={'punctuality': settings['delay_weight']/total,
                                 'energy': settings['energy_weight']/total,
                                 'throughput': 0, 'conflicts': 0, 'arrival_accuracy': 0},
                        delay_scale_s=settings['delay_norm_s'],
                        energy_budget_kwh=settings['energy_norm_kwh'],
                        arrival_tolerance_s=int(settings['arrival_tolerance_s']))


def scenario_for(state):
    scenario = Scenario.model_validate(state['scenario'])
    scenario.now_s = int(state['sim_time_s'])
    scenario.state_version = state['constraint_version'] + 1
    for train in scenario.trains:
        train.priority = max(1, round(state['settings'][train.kind + '_weight'] * 10))
    return scenario


def ui_metrics(value):
    return {'index': value.quality_index or 0, 'total_delay_s': value.total_delay_s,
            'max_delay_s': value.max_delay_s, 'weighted_delay_s': value.weighted_delay_s,
            'passenger_delay_s': value.passenger_delay_s, 'energy_kwh': value.energy_kwh or 0,
            'conflicts': value.conflict_count, 'completed_trips': value.completed_by_window,
            'scheduled_trips': value.scheduled_by_window, 'on_time_pct': value.on_time_fraction*100}


def project_plan(state, scenario, plan, profiles=None, forecast=None):
    profiles = profile_plan(scenario, plan) if profiles is None else profiles
    forecast = calculate_metrics(scenario, plan, config_for(state), profiles=profiles) if forecast is None else forecast
    trains = {t.id: t for t in scenario.trains}
    sections = {s.id: s for s in scenario.sections}
    movements = []
    for move in plan.movements:
        train = trains[move.train_id]
        movements.append({**move.model_dump(), 'leg': train.route.index(move.origin),
                          'release_s': move.end_s + clearance_s(train, sections[move.section_id])})
    return {'id': plan.id, 'label': {'baseline':'Исходный FCFS', 'balanced':'Сбалансированный',
            'passenger':'Приоритет пассажирских', 'eco':'Экономия энергии'}[plan.strategy],
            'status': plan.solver_status.lower(), 'movements': movements, 'metrics': ui_metrics(forecast),
            'calculation_s': plan.elapsed_ms/1000, 'within_budget': plan.elapsed_ms <= 5000,
            'reason': '; '.join(plan.explanations) or 'Пути, физика и ограничения проверены движком logic.',
            'violations': [], 'forecast': forecast.model_dump(),
            '_native': plan.model_dump(), '_profiles': {k:p.model_dump() for k,p in profiles.items()}}


def public_plan(plan):
    return {k:v for k,v in plan.items() if not k.startswith('_')}


def build_plans(state):
    if state.get('engine') != 'logic':
        return demo_planning.build_plans(state)
    scenario = scenario_for(state)
    result = plan_alternatives(scenario, config_for(state),
                               previous=Plan.model_validate(state['active_plan']['_native']),
                               strategies=('balanced', 'passenger', 'eco'), time_budget_s=5)
    return {'plans':[project_plan(state, scenario, c.plan, c.profiles, c.metrics) for c in result.candidates],
            'diagnostics':result.diagnostics, 'message':'; '.join(result.diagnostics),
            'elapsed_s':result.elapsed_ms/1000, 'within_budget':not result.budget_exceeded}


def warm_worker(state):
    if state.get('engine') != 'logic':
        return demo_planning.warm_worker(state)
    return True


def validate_plan(state, plan):
    if state.get('engine') != 'logic':
        return demo_validation.validate_plan(state, plan)
    if state['sim_time_s'] >= state['scenario']['horizon_s']:
        return [{'code':'HORIZON', 'message':'Reset the completed scenario'}]
    return [v.model_dump() for v in native_validate(scenario_for(state), Plan.model_validate(plan['_native']),
                Plan.model_validate(state['active_plan']['_native']))]


def topology(scenario):
    infra = load_infrastructure()
    geometry = json.loads((ROOT/'data/corridor/geometry.geojson').read_text(encoding='utf-8'))['features'][0]['geometry']['coordinates']
    distances = [0.0]
    for a,b in zip(geometry, geometry[1:]):
        lon1,lat1,lon2,lat2 = map(math.radians, (*a[:2], *b[:2]))
        h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        distances.append(distances[-1]+12742000*math.asin(min(1, math.sqrt(h))))
    distances = [d*infra['length_m']/distances[-1] for d in distances]
    def at(distance):
        return [float(np.interp(distance,distances,[p[i] for p in geometry])) for i in (0,1)]
    stations = [{'id':s['id'],'name':s['name'],'position_m':s['distance_m'],
                 'coordinate':at(s['distance_m']),'tracks':len(scenario.stations[i].tracks)}
                for i,s in enumerate(infra['stations'])]
    positions = {s['id']:s['position_m'] for s in stations}
    sections = []
    for s in scenario.sections:
        start,end = positions[s.station_a],positions[s.station_b]
        points = [at(start)] + [p for p,d in zip(geometry,distances) if start<d<end] + [at(end)]
        sections.append({'id':s.id,'from_station':s.station_a,'to_station':s.station_b,
                         'length_m':s.length_m,'speed_limit_mps':s.max_speed_mps,
                         'geometry':points,'main_tracks':[t.model_dump() for t in s.main_tracks]})
    return {'stations':stations,'sections':sections,'length_m':infra['length_m'],
            'assumptions':scenario.metadata['assumptions'],'engine':'logic'}


class LogicSimulator:
    def __init__(self):
        self.reset()

    def reset(self):
        scenario = corridor_scenario()
        result = build_baseline(scenario, time_budget_s=5)
        if result.plan is None:
            raise RuntimeError('No valid initial logic plan: ' + '; '.join(result.diagnostics))
        self.state = {'engine':'logic','scenario':scenario.model_dump(),'topology':topology(scenario),
                      'sim_time_s':0,'state_version':0,'running':False,'speed':30,'incidents':[],
                      'settings':copy.deepcopy(DEFAULT_SETTINGS),'epoch':str(uuid.uuid4()),
                      'constraint_version':0,'awaiting_plan':False}
        self.state['fleet'] = [dict(t.model_dump(), type=t.kind, number=t.id,
            direction=1 if self._position(t.route[-1])>self._position(t.route[0]) else -1,
            destination=t.route[-1]) for t in scenario.trains]
        baseline = project_plan(self.state, scenario, result.plan)
        self.state.update(baseline=copy.deepcopy(baseline), active_plan=baseline)
        self.plans = {}
        self.replanning = False

    def _position(self, station):
        return next(s['position_m'] for s in self.state['topology']['stations'] if s['id']==station)

    def tick(self, seconds=1):
        # The native planner locks all departures <= now. Freeze the simulation
        # clock during a decision so an unapplied schedule can never depart.
        if not self.state['running'] or self.state['awaiting_plan'] or self.replanning:
            return
        end = max(m['release_s'] for m in self.state['active_plan']['movements'])
        self.state['sim_time_s'] = min(end, self.state['sim_time_s']+seconds)
        self.state['state_version'] += 1
        if self.state['sim_time_s'] >= end:
            self.state['running'] = False

    def profile(self, train_id):
        plan = self.state['active_plan']
        points, energy, distance = [],0,0
        moves = sorted((m for m in plan['movements'] if m['train_id']==train_id),key=lambda m:m['leg'])
        if not moves:
            raise KeyError(train_id)
        for m in moves:
            p = plan['_profiles'][f"{train_id}:{m['section_id']}"]
            for point in p['points']:
                fraction = point['time_s']/p['duration_s'] if p['duration_s'] else 0
                points.append([m['start_s']+point['time_s'],distance+point['position_m'],point['speed_mps'],
                               point['limit_mps'],energy+fraction*p['energy_kwh']])
            energy += p['energy_kwh']
            distance += p['points'][-1]['position_m']
        return {'train_id':train_id,'plan_id':plan['id'],'points':points,'energy_kwh':energy,
                'arrival_s':moves[-1]['end_s'],'reachable':True,
                'assumptions':['Traction and auxiliary energy from logic; within-leg cumulative energy is time-interpolated.']}

    def snapshot(self):
        state, fleet = self.state, []
        now, plan = state['sim_time_s'], state['active_plan']
        for train in state['fleet']:
            item = copy.deepcopy(train)
            item.update(position_m=self._position(train['route'][0]),speed_mps=0,energy_kwh=0,
                        status='waiting',station_id=train['route'][0],section_id=None,delay_s=0,next_leg=0)
            moves = sorted((m for m in plan['movements'] if m['train_id']==train['id']),key=lambda m:m['leg'])
            for m in moves:
                if m['start_s']>now:
                    break
                p = plan['_profiles'][f"{train['id']}:{m['section_id']}"]
                elapsed = min(now-m['start_s'],p['duration_s'])
                points = p['points']
                times = [x['time_s'] for x in points]
                item['position_m'] = self._position(m['origin'])+train['direction']*float(np.interp(elapsed,times,[x['position_m'] for x in points]))
                item['speed_mps'] = float(np.interp(elapsed,times,[x['speed_mps'] for x in points]))
                item['energy_kwh'] += p['energy_kwh'] * elapsed/p['duration_s']
                item['next_leg'] = m['leg'] if now<m['end_s'] else m['leg']+1
                if now<m['end_s']:
                    item.update(status='moving',section_id=m['section_id'],station_id=None,main_track_id=m['main_track_id'])
                    break
                item.update(status='completed' if m==moves[-1] else 'waiting',section_id=None,
                            station_id=m['destination'],speed_mps=0,position_m=self._position(m['destination']))
            # Factual terminal delay only; forecasts are in plan.metrics.
            due = train['due_s']
            item['delay_s'] = max(0,min(now,moves[-1]['end_s'])-due)
            item['eta_s'] = None if state['awaiting_plan'] else moves[-1]['end_s']
            fleet.append(item)
        sections=[]
        for section in state['topology']['sections']:
            active=[i for i in state['incidents'] if i['target_id']==section['id'] and i['start_s']<=now<i['end_s']]
            occupied=[m for m in plan['movements'] if m['section_id']==section['id'] and m['start_s']<=now<m['release_s']]
            sections.append({'id':section['id'],'status':'closed' if any(i['kind']=='closure' for i in active) else 'signal_failure' if active else 'occupied' if occupied else 'open',
                             'occupying':[m['train_id'] for m in occupied], 'signal':'red' if active or occupied else 'green',
                             'tracks':[dict(t,occupying=[m['train_id'] for m in occupied if m['main_track_id']==t['id']]) for t in section['main_tracks']]})
        actual = copy.deepcopy(plan['metrics'])
        completed=[t for t in fleet if t['status']=='completed']
        weighted=sum(t['delay_s']*state['settings'][t['type']+'_weight'] for t in fleet)
        energy=sum(t['energy_kwh'] for t in fleet)
        settings=state['settings']
        denominator=settings['delay_weight']+settings['energy_weight']
        index=100*(1-(settings['delay_weight']*min(1,weighted/settings['delay_norm_s'])+settings['energy_weight']*min(1,energy/settings['energy_norm_kwh']))/denominator)
        actual.update(index=index,total_delay_s=sum(t['delay_s'] for t in fleet),max_delay_s=max(t['delay_s'] for t in fleet),
                      weighted_delay_s=weighted,passenger_delay_s=sum(t['delay_s'] for t in fleet if t['type']=='passenger'),
                      energy_kwh=energy,completed_trips=len(completed),scheduled_trips=sum(t['due_s']<=now for t in fleet),
                      on_time_pct=100*sum(abs(t['eta_s']-t['due_s'])<=settings['arrival_tolerance_s'] for t in completed)/len(completed) if completed and not state['awaiting_plan'] else None)
        return {k:state[k] for k in ('sim_time_s','state_version','epoch','running','speed','incidents','awaiting_plan')} | {
            'engine':'logic','decision_hold':state['awaiting_plan'] or self.replanning,
            'trains':fleet,'sections':sections,'switches':[], 'metrics':actual,
            'active_plan_id':plan['id'],'plan':public_plan(plan),'replanning':self.replanning}

    def add_incident(self, kind, target_id, duration_s):
        scenario = scenario_for(self.state)
        now = scenario.now_s
        start = now + 1
        until = min(scenario.horizon_s,now+duration_s)
        if until<=now:
            raise ValueError('Reset the completed scenario')
        identifier=str(uuid.uuid4())
        if kind=='delay':
            item=next((t for t in self.snapshot()['trains'] if t['id']==target_id and t['status']=='waiting'),None)
            if not item:
                raise ValueError('Choose a train waiting at a station')
            train=next(t for t in scenario.trains if t.id==target_id)
            # A departure at exactly now is already committed by the native model.
            train.not_before_s[item['station_id']]=until
        else:
            section=next((s for s in scenario.sections if s.id==target_id),None)
            if section is None:
                raise KeyError(target_id)
            if kind=='speed_restriction':
                section.entry_speed_limits.append(EntrySpeedLimit(id=identifier,start_s=now+1,end_s=until,speed_factor=.5,reason='Dispatcher speed restriction'))
            else:
                # Closures begin after tails already inside have cleared.
                if kind=='closure':
                    start=max([now+1]+[m['release_s'] for m in self.state['active_plan']['movements'] if m['section_id']==target_id and m['start_s']<=now<m['release_s']])
                until=max(until,start+duration_s)
                if until>=scenario.horizon_s:
                    raise ValueError('Incident extends beyond the planning horizon')
                scenario.blocks.append(Block(id=identifier,resource='section:'+target_id,start_s=start,end_s=until,kind=kind))
        self.state['scenario']=scenario.model_dump()
        entry={'id':identifier,'kind':kind,'target_id':target_id,'duration_s':duration_s,'start_s':now if kind=='delay' else start,'end_s':until,'created_s':now}
        self.state['incidents'].append(entry)
        self.state['state_version']+=1
        self.state['constraint_version']+=1
        self.state['awaiting_plan']=True
        self.plans.clear()
        return entry

    def load_case(self, kind):
        self.reset()
        scenario=incident_scenario(Scenario.model_validate(self.state['scenario']),Plan.model_validate(self.state['active_plan']['_native']),kind)
        self.state['scenario']=scenario.model_dump()
        self.state['sim_time_s']=scenario.now_s
        self.state['state_version']+=1
        self.state['constraint_version']+=1
        self.state['awaiting_plan']=True
        info=scenario.metadata.get('incident',{})
        self.state['incidents']=[{'id':str(uuid.uuid4()),'kind':kind,'target_id':info.get('resource','scenario'),
                                 'start_s':scenario.now_s,'end_s':scenario.horizon_s, 'details':info}]
