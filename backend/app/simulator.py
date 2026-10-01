import copy
import json
import uuid
from .domain import ROOT, topology, trains, stops, movement_profile, started
from .advisory import sample
from .metrics import DEFAULT_SETTINGS, metrics
from .planning import build_plans
from .validation import validate_plan
from .railway_map import train_map_data
from .timing import LIVE_REPLAN_SPEED_LIMIT
from .speed_advice import live_advice


class Simulator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = {'topology':topology(),'fleet':trains(),'sim_time_s':0,'state_version':0,
                      'running':False,'speed':30,'incidents':[],'committed':[],
                      'settings':copy.deepcopy(DEFAULT_SETTINGS),'epoch':str(uuid.uuid4()),
                      'constraint_version':0,'awaiting_plan':False,
                      'replanning_options':{'auto_apply':True,'policy':'balanced'},
                      'replan_status':{'status':'idle','comparison':None,'message':None}}
        fixture = ROOT/'scenarios/baseline-plan.json'
        if fixture.exists():
            baseline = json.loads(fixture.read_text(encoding='utf-8'))
            if validate_plan(self.state,baseline):
                raise RuntimeError('Baseline scenario changed; regenerate baseline with scripts/freeze_baseline.py')
        else:
            result = build_plans(copy.deepcopy(self.state))
            if not result['plans']:
                raise RuntimeError('Initial scenario has no validated plan')
            baseline = result['plans'][0]
        self.state['baseline'] = copy.deepcopy(baseline)
        baseline['metrics'] = metrics(self.state,baseline)
        self.state['baseline']['metrics'] = copy.deepcopy(baseline['metrics'])
        self.state['active_plan'] = baseline
        self.plans = {}
        self.replanning = False

    @property
    def clock_held_for_replan(self):
        return self.state['running'] and self.state['speed']>LIVE_REPLAN_SPEED_LIMIT and (self.replanning or self.state['awaiting_plan'])

    def tick(self,seconds=1):
        if not self.state['running'] or self.clock_held_for_replan or seconds<=0:
            return
        now=self.state['sim_time_s']
        movements=self.state['active_plan']['movements']
        target=now+seconds
        if not self.state['awaiting_plan'] or all(started(self.state,m) for m in movements):
            target=max(now,min(target,max(m['release_s'] for m in movements)))
        if not self.state['awaiting_plan'] and target>now:
            # Equivalent to one-second ticks, including departure boundary/order,
            # without a loop proportional to the selected speed.
            due=[m for m in movements if m['start_s']<target and not started(self.state,m)]
            for m in sorted(due,key=lambda m:max(now,m['start_s'])):
                self.state['committed'].append([m['train_id'],m['leg']])
        self.state['sim_time_s']=target
        self.state['state_version']+=target-now
        if all(started(self.state,m) and m['release_s']<=target for m in movements):
            self.state['running']=False

    def snapshot(self, violations=None):
        state = self.state
        now = state['sim_time_s']
        topo = state['topology']
        if violations is None:
            violations=validate_plan(state,state['active_plan'])
        fleet = []
        for train in state['fleet']:
            item = copy.deepcopy(train)
            route = stops(train)
            item.update(position_m=topo['stations'][route[0]]['position_m'],speed_mps=0,energy_kwh=0,
                        status='waiting',station_id=topo['stations'][route[0]]['id'],section_id=None,delay_s=0,next_leg=0)
            legs = sorted([m for m in state['active_plan']['movements'] if m['train_id']==train['id']],key=lambda m:m['leg'])
            for m in legs:
                if not started(state,m):
                    break
                section = next(s for s in topo['sections'] if s['id']==m['section_id'])
                p = movement_profile(train,section,m['end_s']-m['start_s'])
                distance,speed,energy = sample(p,min(p['duration_s'],max(0,now-m['start_s'])))
                item['energy_kwh'] += energy
                origin = topo['stations'][route[m['leg']]]['position_m']
                item['position_m'] = origin+train['direction']*distance
                item['speed_mps'] = speed
                item['next_leg'] = m['leg'] if now<m['end_s'] else m['leg']+1
                if now<m['end_s']:
                    item.update(status='moving',section_id=m['section_id'],station_id=None)
                    break
                item.update(status='completed' if m['leg']==4 else 'waiting',section_id=None,
                            station_id=topo['stations'][route[m['leg']+1]]['id'],speed_mps=0)
            base_legs = [m for m in state['baseline']['movements'] if m['train_id']==train['id']]
            for base in base_legs:
                actual = next(m for m in legs if m['leg']==base['leg'])
                if started(state,actual) and actual['end_s']<=now:
                    item['delay_s']=max(item['delay_s'],actual['end_s']-base['end_s'])
                elif base['end_s']<now:
                    item['delay_s']=max(item['delay_s'],now-base['end_s'])
            item['eta_s'] = legs[-1]['end_s'] if not state['awaiting_plan'] else None
            item.update(train_map_data(topo,item))
            item['speed_advice']=live_advice(state,train,item,plan_valid=not violations)
            fleet.append(item)
        sections = []
        for section in topo['sections']:
            active = [i for i in state['incidents'] if i['target_id']==section['id'] and i['start_s']<=now<i['end_s']]
            occupying = [m['train_id'] for m in state['active_plan']['movements'] if m['section_id']==section['id'] and started(state,m) and m['start_s']<=now<m['release_s']]
            status = 'closed' if any(i['kind']=='closure' for i in active) else ('signal_failure' if active else ('occupied' if occupying else 'open'))
            sections.append({'id':section['id'],'status':status,'occupying':occupying,'signal':'red' if active or occupying else 'green'})
        switches = []
        for si,station in enumerate(topo['stations']):
            active = None
            for train in state['fleet']:
                route = stops(train)
                for m in state['active_plan']['movements']:
                    if m['train_id']!=train['id'] or not started(state,m):
                        continue
                    if (route[m['leg']]==si and m['start_s']<=now<m['start_s']+15) or (route[m['leg']+1]==si and m['end_s']-15<=now<m['end_s']):
                        active=train['id']
            switches.append({'id':f'switch-{station["id"]}','station_id':station['id'],'position':'route' if active else 'normal','available':active is None,'train_id':active})
        actual = metrics(state,state['active_plan'],False,violations=violations)
        actual['energy_kwh'] = round(sum(t['energy_kwh'] for t in fleet),2)
        return {'sim_time_s':now,'state_version':state['state_version'],'epoch':state['epoch'],
                'constraint_version':state['constraint_version'],
                'running':state['running'],'speed':state['speed'],'trains':fleet,'sections':sections,'switches':switches,
                'metrics':actual,'active_plan_id':state['active_plan']['id'],'plan':state['active_plan'],
                'incidents':[{**i,'status':'resolved' if 'resolved_s' in i else 'expired' if i['end_s']<=now else 'active'} for i in state['incidents']],
                'awaiting_plan':state['awaiting_plan'],'replanning':self.replanning,
                'replanning_options':state['replanning_options'],'replan_status':copy.deepcopy(state['replan_status']),
                'clock_held_for_replan':self.clock_held_for_replan}
