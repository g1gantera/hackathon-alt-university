"""Transparent rolling-window quality. Optimization never relaxes constraints."""
from collections import deque


class Quality:
    WEIGHTS={'schedule':.30,'capacity':.20,'energy':.20,'conflicts':.20,'stopping':.10}

    def __init__(self):
        self.samples=deque()
        self.last_sample=-1
        self.previous=None
        self.reasons=['Awaiting simulation observations']

    def sample(self,engine):
        if not engine.trains:
            return
        if int(engine.sim_time)==self.last_sample:
            return
        self.last_sample=int(engine.sim_time)
        eligible=[t for t in engine.trains.values() if t.spec.departure_s<=engine.sim_time and t.state not in {'arrived','completed','dwelling'}]
        lateness=[engine.delay(t) for t in engine.trains.values() if t.spec.departure_s<=engine.sim_time]
        self.samples.append(dict(time=engine.sim_time,delay=sum(lateness)/max(1,len(lateness)),moving=sum(t.speed>.3 for t in eligible),eligible=len(eligible),energy=sum(t.energy for t in engine.trains.values()),reference=sum(t.reference_energy for t in engine.trains.values()),conflicts=len(engine.invariant_errors())+bool(engine.deadlock),stop_error=max([abs(a['error_m']) for t in engine.trains.values() for a in t.arrivals if engine.sim_time-a['time']<=engine.config.quality_window_s] or [0])))
        while self.samples and self.samples[0]['time']<engine.sim_time-engine.config.quality_window_s:
            self.samples.popleft()

    def result(self,engine):
        ss=list(self.samples)
        if not ss:
            return {'score':None,'status':'No observations','factors':{},'reasons':self.reasons,'window_s':engine.config.quality_window_s}
        n=len(ss)
        demand=sum(s['eligible'] for s in ss)
        energy=max(0,ss[-1]['energy']-ss[0]['energy'])
        reference=max(0,ss[-1]['reference']-ss[0]['reference'])
        values={
            'schedule':max(0,1-sum(s['delay'] for s in ss)/n/300),
            'capacity':sum(s['moving'] for s in ss)/demand if demand else 1,
            'energy':min(1,reference/energy) if energy>1e-6 else 1,
            'conflicts':max(0,1-sum(s['conflicts']>0 for s in ss)/n),
            'stopping':max(0,1-max(s['stop_error'] for s in ss)/5),
        }
        factors={k:{'value':round(v*100,1),'weight':self.WEIGHTS[k],'contribution':round(v*self.WEIGHTS[k]*100,2)} for k,v in values.items()}
        score=round(sum(v['contribution'] for v in factors.values()),1)
        reasons=[f'{k}: {v["value"]:.1f}/100' for k,v in factors.items() if v['value']<99.9]
        return {'score':score,'status':'Normal' if score>=engine.config.normal_threshold else 'Attention' if score>=engine.config.attention_threshold else 'Critical','factors':factors,'reasons':reasons or ['No penalty observed in this window'],'window_s':engine.config.quality_window_s,'observed_s':round(ss[-1]['time']-ss[0]['time'],1),'samples':n}
