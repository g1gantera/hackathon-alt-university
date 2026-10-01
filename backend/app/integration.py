"""Bridge the logic branch's typed planner to the live dispatcher's API.

The legacy demo remains selectable with DISPATCH_ENGINE=demo. Native plans,
profiles and constraints are retained; UI projections never drive validation.
"""
import copy
import json
import math
import time
import uuid

import numpy as np

from .switches import states as switch_states
from .traffic_control import CATEGORIES, control_state, journal_events
from .domain import ROOT
from .control_mode import limit_time, authorized
from .corridors import CORRIDORS
from .station_capacity import expanded_stations
from .traffic import daily_demand
from . import demo_planning, demo_validation
from .metrics.quality import MetricConfig, calculate_metrics, profile_plan
from .live_logic import prepare_profiles, sample_profile, waiting_energy, resource_states, actual_metrics
from .planning.baseline import build_baseline
from .planning.common import clearance_s
from .planning.service import plan_alternatives
from .schemas import Block, EntrySpeedLimit, Plan, Scenario
from .scenarios import corridor_scenario, load_infrastructure, incident_scenario, INCIDENTS
from .validation.plan import validate_plan as native_validate
from .track_display import display_tracks, dispatch_state


NATIVE_CONFIG = MetricConfig.load(ROOT/'config/metrics.json')


def default_settings():
    return {'passenger_weight': 3, 'freight_weight': 1,
            'delay_weight': NATIVE_CONFIG.weights['punctuality'],
            'energy_weight': NATIVE_CONFIG.weights['energy'],
            'delay_norm_s': NATIVE_CONFIG.delay_scale_s,
            'energy_norm_kwh': NATIVE_CONFIG.energy_budget_kwh,
            'arrival_tolerance_s': NATIVE_CONFIG.arrival_tolerance_s}


def config_for(state):
    if 'metric_config' in state:
        return MetricConfig.model_validate(state['metric_config'])
    settings = state['settings']
    config = NATIVE_CONFIG.model_copy(deep=True)
    # The existing two UI controls divide their native combined share (0.5).
    # Throughput, conflicts and arrival accuracy retain their native weights.
    share = config.weights['punctuality'] + config.weights['energy']
    total = settings['delay_weight'] + settings['energy_weight']
    config.weights.update(punctuality=share*settings['delay_weight']/total,
                          energy=share*settings['energy_weight']/total)
    config.delay_scale_s = settings['delay_norm_s']
    config.energy_budget_kwh = settings['energy_norm_kwh']
    config.arrival_tolerance_s = int(settings['arrival_tolerance_s'])
    return config


def scenario_for(state):
    scenario = Scenario.model_validate(state['scenario'])
    scenario.now_s = int(state['sim_time_s'])
    scenario.state_version = state['constraint_version'] + 1
    for train in scenario.trains:
        train.priority = CATEGORIES[train.dispatch_category][1] if train.dispatch_category else max(1, round(state['settings'][train.kind + '_weight']))
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
            'status': plan.solver_status.lower(), 'movements': movements,
            'stops': [s.model_dump() for s in plan.stops], 'metrics': ui_metrics(forecast),
            'calculation_s': plan.elapsed_ms/1000, 'within_budget': plan.elapsed_ms <= 5000,
            'reason': '; '.join(plan.explanations) or 'Пути, физика и ограничения проверены движком logic.',
            'violations': [], 'forecast': forecast.model_dump(),
            '_native': plan.model_dump(), '_profiles': prepare_profiles(scenario, plan, profiles)}


def public_plan(plan):
    return {k:v for k,v in plan.items() if not k.startswith('_')}


def build_plans(state):
    if state.get('engine') != 'logic':
        return demo_planning.build_plans(state)
    started = time.perf_counter()
    scenario = scenario_for(state)
    # Reserve time within the live five-second target for API projection and IPC.
    network=scenario.metadata.get('network',False)
    budget=state.get('planning_budget_s',15 if network else 5)
    result = plan_alternatives(scenario, config_for(state),
                               previous=Plan.model_validate(state['active_plan']['_native']),
                               strategies=('balanced',) if network else ('balanced', 'passenger', 'eco'), time_budget_s=max(1,budget-.8))
    plans = [project_plan(state, scenario, c.plan, c.profiles, c.metrics) for c in result.candidates]
    elapsed = time.perf_counter()-started
    return {'plans':plans, 'diagnostics':result.diagnostics, 'message':'; '.join(result.diagnostics),
            'elapsed_s':elapsed, 'within_budget':elapsed <= 5}


def compact_state(state):
    """Copy mutable scheduling data; workers/reporting do not need live profile arrays."""
    data = {k: v for k, v in state.items() if k not in ('active_plan', 'baseline')}
    for key in ('active_plan', 'baseline'):
        if key in state:
            data[key] = {k: v for k, v in state[key].items() if k != '_profiles'}
    return copy.deepcopy(data)


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
    if scenario.metadata.get('network'):
        from .regional import regional_topology
        return regional_topology(scenario)
    corridor=scenario.metadata.get('corridor_key','kokshetau')
    infra = load_infrastructure(CORRIDORS[corridor][1])
    features = json.loads((ROOT/'data/corridor/geometry.geojson').read_text(encoding='utf-8'))['features']
    geometry = infra.get('geometry') or features[0]['geometry']['coordinates']
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
    display_tracks(stations, sections, scenario, infra, features)
    return {'corridor_id':corridor,'name':infra['name'],'stations':stations,'sections':sections,'length_m':infra['length_m'],
            'track_geometry_note':'Номера путей и привязка к OSM условные; геометрия перегонных путей интерполирована. Пропускная способность задаётся моделью, не числом линий OSM.',
            'assumptions':scenario.metadata['assumptions'],'engine':'logic'}


class LogicSimulator:
    def __init__(self, corridor='kokshetau', traffic_profile='demo', service_date='2026-10-02'):
        self._corridor=corridor
        self._traffic_profile=traffic_profile
        self._service_date=service_date
        self.reset()

    def reset(self):
        if self._corridor=="akmola_network":
            from .regional import regional_scenario
            scenario=regional_scenario(self._service_date)
            if self._traffic_profile=="reference_day":
                from .regional_traffic import reference_demand
                scenario=reference_demand(scenario,self._service_date)
        else:
            scenario = expanded_stations(corridor_scenario(CORRIDORS[self._corridor][1]))
        if self._traffic_profile=="reference_day" and self._corridor!="akmola_network":
            scenario=daily_demand(scenario,self._service_date)
        for train in scenario.trains:
            train.dispatch_category=train.kind
            train.priority=CATEGORIES[train.kind][1]
        result = build_baseline(scenario, time_budget_s=5)
        if result.plan is None:
            raise RuntimeError('No valid initial logic plan: ' + '; '.join(result.diagnostics))
        self.state = {'engine':'logic','scenario':scenario.model_dump(),'topology':topology(scenario),
                      'control_mode':'manual','route_clearances':[], 'sim_time_s':0,'state_version':0,'running':False,'speed':30,'incidents':[],
                      'settings':default_settings(),'epoch':str(uuid.uuid4()),
                      'constraint_version':0,'awaiting_plan':False, 'baseline_scenario':scenario.model_dump()}
        from .metric_settings import load_override
        override = load_override()
        if override is not None:
            self.state['metric_config'] = override.model_dump()
        self.state['fleet'] = [dict(t.model_dump(), type=t.kind, number=t.id,
            direction=1 if self._position(t.route[-1])>self._position(t.route[0]) else -1,
            destination=t.route[-1]) for t in scenario.trains]
        baseline = project_plan(self.state, scenario, result.plan)
        self.state.update(baseline=copy.deepcopy(baseline), active_plan=baseline)
        self.plans = {}
        self.replanning = False
        self._validation_cache = None

    def _position(self, station):
        return next(s['position_m'] for s in self.state['topology']['stations'] if s['id']==station)

    def tick(self, seconds=1):
        # The native planner locks all departures <= now. Freeze the simulation
        # clock during a decision so an unapplied schedule can never depart.
        if not self.state['running'] or self.state['awaiting_plan'] or self.replanning:
            return
        plan = self.state['active_plan']
        end = max(max(m['release_s'] for m in plan['movements']),
                  max(s['departure_s'] for s in plan['_native']['stops']))
        self.state['sim_time_s'] = min(end, limit_time(self.state, self.state['sim_time_s']+seconds))
        self.state['state_version'] += 1
        if self.state['sim_time_s'] >= end:
            self.state['running'] = False

    def plan_violations(self):
        # Future constraints change only with constraint_version or a new plan.
        # Every apply is independently checked again at the current simulation time.
        key = (self.state['epoch'], self.state['constraint_version'], self.state['active_plan']['id'])
        if self._validation_cache is None or self._validation_cache[0] != key:
            errors = validate_plan(self.state, self.state['active_plan'])
            scenario = scenario_for(self.state)
            native = Plan.model_validate(self.state['active_plan']['_native'])
            if native.state_version != scenario.state_version:
                checked = native.model_copy(update={'state_version': scenario.state_version})
                errors += [v.model_dump() for v in native_validate(scenario, checked, native)]
            self._validation_cache = (key, errors)
        return self._validation_cache[1]

    def active_public_plan(self):
        result = public_plan(self.state['active_plan'])
        violations = self.plan_violations()
        result['violations'] = violations
        result['applicable'] = not violations
        result['needs_replan'] = self.state['awaiting_plan'] or bool(violations)
        if violations:
            # A historical forecast must not masquerade as valid after an incident.
            result['forecast'] = dict(result['forecast'], applicable=False, track_load=None,
                                      quality_index=None, energy_kwh=None, category='Критично',
                                      violation_count=len(violations))
        return result

    def profile(self, train_id):
        plan = self.state['active_plan']
        train = next((t for t in self.state['scenario']['trains'] if t['id']==train_id), None)
        if train is None:
            raise KeyError(train_id)
        stops = sorted((s for s in plan['_native']['stops'] if s['train_id']==train_id),
                       key=lambda s:train['route'].index(s['station_id']))
        moves = sorted((m for m in plan['movements'] if m['train_id']==train_id),key=lambda m:m['leg'])
        points, energy, distance = [], 0.0, 0.0
        # Include off-network waiting and each dwell so totals equal native metrics.
        points.append([train['release_s'],0,0,0,0])
        for index, stop in enumerate(stops):
            for time in (stop['arrival_s'], stop['departure_s']):
                points.append([time,distance,0,0,energy+waiting_energy(train,stops,time)])
            if index == len(moves):
                break
            m = moves[index]
            p = plan['_profiles'][f"{train_id}:{m['section_id']}"]
            wait = waiting_energy(train,stops,m['start_s'])
            for point in p['points']:
                _, _, consumed = sample_profile(p,point['time_s'])
                points.append([m['start_s']+point['time_s'],distance+point['position_m'],
                               point['speed_mps'],point['limit_mps'],energy+wait+consumed])
            energy += p['energy_kwh']
            distance += p['points'][-1]['position_m']
        errors = self.plan_violations()
        return {'train_id':train_id,'plan_id':plan['id'],'points':points,
                'energy_kwh':energy+waiting_energy(train,stops,stops[-1]['departure_s']),
                'arrival_s':moves[-1]['end_s'],'reachable':not errors,
                'applicable':not errors,'violations':errors,
                'assumptions':['Native constant-acceleration cells, traction work and auxiliary power; '
                               'includes station, recovery and off-network waiting; no regeneration.']}

    def snapshot(self):
        state, fleet = self.state, []
        now, plan = state['sim_time_s'], state['active_plan']
        native_trains = {t.id:t for t in scenario_for(state).trains}
        for train in state['fleet']:
            item = copy.deepcopy(train)
            item.update(position_m=self._position(train['route'][0]),speed_mps=0,energy_kwh=0,
                        status='waiting',station_id=train['route'][0],section_id=None,delay_s=0,next_leg=0,
                        distance_travelled_m=0.0,priority=native_trains[train['id']].priority,
                        holding=False,arrival_s=None)
            moves = sorted((m for m in plan['movements'] if m['train_id']==train['id']),key=lambda m:m['leg'])
            for m in moves:
                if m['start_s']>now:
                    break
                p = plan['_profiles'][f"{train['id']}:{m['section_id']}"]
                position, speed, energy = sample_profile(p,now-m['start_s'])
                origin_pos,dest_pos=self._position(m['origin']),self._position(m['destination'])
                item['position_m'] = origin_pos+(dest_pos-origin_pos)*position/max(1,p['points'][-1]['position_m'])
                section=next(s for s in state['topology']['sections'] if s['id']==m['section_id'])
                item['direction'] = 1 if section['from_station']==m['origin'] else -1
                item['distance_travelled_m'] += position
                item['speed_mps'] = speed
                item['energy_kwh'] += energy
                item['next_leg'] = m['leg'] if now<m['end_s'] else m['leg']+1
                if now<m['end_s']:
                    item.update(status='moving',section_id=m['section_id'],station_id=None,
                                main_track_id=m['main_track_id'],holding=now<m['start_s']+m['hold_s'])
                    break
                item.update(status='completed' if m==moves[-1] else 'waiting',section_id=None,
                            station_id=m['destination'],speed_mps=0,position_m=self._position(m['destination']))
            stops = sorted((s for s in plan['_native']['stops'] if s['train_id']==train['id']),
                           key=lambda s:train['route'].index(s['station_id']))
            item.update(dispatch_state(train, stops, moves, plan['movements'], now))
            item['energy_kwh'] += waiting_energy(train,stops,now)
            item['delay_s'] = max(0,min(now,moves[-1]['end_s'])-train['due_s'])
            item['eta_s'] = None if state['awaiting_plan'] else moves[-1]['end_s']
            item['arrival_s'] = moves[-1]['end_s'] if item['status']=='completed' else None
            fleet.append(item)
        sections, stations = resource_states(state)
        violations = self.plan_violations()
        actual = actual_metrics(state, fleet, config_for(state), violations)
        switches = switch_states(state)
        signals, yields = control_state(state, fleet, switches, not violations and not state['awaiting_plan'] and not self.replanning)
        if state['scenario']['metadata'].get('network'):
            from .block_sections import signal_states
            signals.extend(signal_states(scenario_for(state),Plan.model_validate(plan['_native']),now,not violations and not state['awaiting_plan']))
        manual_hold=any(now < m['start_s'] <= now+0.01 and not authorized(state,m) for m in plan['movements'])
        return {k:state[k] for k in ('sim_time_s','state_version','epoch','running','speed','incidents','awaiting_plan')} | {
            'planning_budget_s':state.get('planning_budget_s',15 if state['scenario']['metadata'].get('network') else 5),
            'control_mode':state.get('control_mode','manual'), 'pending_departures':[dict(m, authorized=authorized(state,m)) for m in plan['movements'] if m['start_s']>now],
            'track_wear':state['scenario'].get('metadata',{}).get('track_wear',{}),'engine':'logic','traffic':state['scenario'].get('metadata',{}).get('traffic',{}),'station_capacity':state['scenario'].get('metadata',{}).get('station_capacity',{}),'manual_hold':manual_hold,'decision_hold':state['awaiting_plan'] or self.replanning or manual_hold,
            'trains':fleet,'sections':sections,'stations':stations,'switches':switches, 'signals':signals, 'dispatch_events':sorted({e['id']:e for e in journal_events(state, yields)+state.get('_dispatch_journal', [])}.values(), key=lambda e:e['sim_time_s'])[-500:], 'metrics':actual,
            'active_plan_id':plan['id'],'plan':self.active_public_plan(),'replanning':self.replanning}

    def update_settings(self, settings):
        settings = dict(settings)
        for kind in ('passenger', 'freight'):
            settings[kind+'_weight'] = max(1, round(settings[kind+'_weight']))
        settings['arrival_tolerance_s'] = int(settings['arrival_tolerance_s'])
        self.state['settings'] = settings
        self.state['constraint_version'] += 1
        self.state['state_version'] += 1
        for key in ('active_plan', 'baseline'):
            plan = self.state[key]
            if key == 'baseline':
                base_state = dict(self.state, scenario=self.state['baseline_scenario'], sim_time_s=0)
                scenario = scenario_for(base_state)
            else:
                scenario = scenario_for(self.state)
            native = Plan.model_validate(plan['_native'])
            # Settings change scoring/priorities, but cannot change a reservation.
            # A plan invalidated by a physical incident remains invalid.
            checked = native.model_copy(update={'state_version':scenario.state_version})
            errors = native_validate(scenario, checked, native if key=='active_plan' else None)
            if not errors:
                plan['_native'] = checked.model_dump()
                forecast = calculate_metrics(scenario,checked,config_for(self.state),
                                             previous=native if key=='active_plan' else None)
                plan['forecast'], plan['metrics'] = forecast.model_dump(), ui_metrics(forecast)
        self.plans.clear()
        self._validation_cache = None

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
        self.state['topology']=topology(scenario)
        self.state['sim_time_s']=scenario.now_s
        self.state['state_version']+=1
        self.state['constraint_version']+=1
        self.state['awaiting_plan']=True
        info=scenario.metadata.get('incident',{})
        intervals = [b.model_dump() for b in scenario.blocks]
        intervals += [dict(limit.model_dump(), resource='section:'+section.id, kind='speed_restriction')
                      for section in scenario.sections for limit in section.entry_speed_limits]
        until = max((interval['end_s'] for interval in intervals), default=scenario.horizon_s)
        self.state['incidents']=[{'id':str(uuid.uuid4()),'kind':kind,'target_id':info.get('resource','scenario'),
                                 'start_s':scenario.now_s,'end_s':until, 'details':info,
                                 'constraint_intervals':intervals}]
