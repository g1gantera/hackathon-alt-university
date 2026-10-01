"""One replanning worker; latest inputs win, validation gates every application."""
import asyncio
import copy
import logging
import time
import uuid
from datetime import datetime, timezone

from .domain import started
from .metrics import metrics
from .planning import objective_value
from .validation import validate_plan

logger=logging.getLogger('dispatch')


def comparison(state, plan, basis='forecast_at_evaluation'):
    """Compare both schedules at the same model time, constraints and formula."""
    old=state['active_plan']
    old_moves={(m['train_id'],m['leg']):m for m in old['movements']}
    new_moves={(m['train_id'],m['leg']):m for m in plan['movements']}
    committed=[m for m in old['movements'] if started(state,m)]
    changes=[]
    for m in plan['movements']:
        previous=old_moves[(m['train_id'],m['leg'])]
        if any(m[key]!=previous[key] for key in ('start_s','end_s','release_s')):
            changes.append({'train_id':m['train_id'],'leg':m['leg'],'section_id':m['section_id'],
                            'before_s':previous['start_s'],'after_s':m['start_s'],'change_s':m['start_s']-previous['start_s'],
                            'before_arrival_s':previous['end_s'],'after_arrival_s':m['end_s'],
                            'before_release_s':previous['release_s'],'after_release_s':m['release_s']})
    trains=[]
    for train in state['fleet']:
        before=max(m['end_s'] for m in old['movements'] if m['train_id']==train['id'])
        after=max(m['end_s'] for m in plan['movements'] if m['train_id']==train['id'])
        trains.append({'train_id':train['id'],'before_arrival_s':before,'after_arrival_s':after,'change_s':after-before})
    before=metrics(state,old,violations=validate_plan(state,old))
    after=metrics(state,plan,violations=validate_plan(state,plan))
    return {'version':1,'basis':basis,'evaluated_at_s':state['sim_time_s'],
            'epoch':state['epoch'],'constraint_version':state['constraint_version'],
            'old_plan_id':old['id'],'new_plan_id':plan['id'],
            'old_plan_label':old.get('label',old['id']),'new_plan_label':plan.get('label',plan['id']),
            'calculation_s':plan.get('calculation_s'),
            'changes':changes,'trains':trains,
            'committed_total':len(committed),
            'committed_preserved':sum(new_moves.get((m['train_id'],m['leg']))==m for m in committed),
            'violations_before':before['conflicts'],'before':before,'after':after}


class Replanner:
    def __init__(self, sim, solve, emit, publish, save_plan, debounce=.3):
        self.sim,self.solve,self.emit,self.publish,self.save_plan=sim,solve,emit,publish,save_plan
        self.debounce=debounce
        self.task=None
        self.pending=None
        self.closing=False

    def status(self, **fields):
        self.sim.state['replan_status'].update(fields)
        self.sim.state['state_version']+=1

    def request(self, trigger='manual', automatic=False):
        if trigger=='manual' and self.sim.replanning:
            return {'job_id':self.sim.state['replan_status'].get('job_id'),'status':'running'}
        request={'job_id':str(uuid.uuid4()),'trigger':trigger,'automatic':automatic}
        self.pending=request
        self.sim.replanning=True
        self.sim.plans.clear()
        self.sim.state['replan_status']={**request,'status':'queued','attempt':0,
            'policy':self.sim.state['replanning_options']['policy'],'comparison':None,'message':None,
            'incident_ids':[i['id'] for i in self.sim.state['incidents']],
            'requested_at':datetime.now(timezone.utc).isoformat()}
        self.sim.state['state_version']+=1
        self.emit('replan.queued',request)
        self.publish()
        if self.task is None or self.task.done():
            self.task=asyncio.create_task(self.run())
        return {'job_id':request['job_id'],'status':'queued'}

    def invalidate(self):
        # Running process work can finish, but its result no longer has authority.
        self.pending=None
        self.sim.replanning=False

    def install(self, plan, automatic=False):
        state=self.sim.state
        if plan.get('epoch')!=state['epoch'] or plan.get('constraint_version')!=state['constraint_version']:
            raise ValueError('Plan conditions changed; recalculate')
        if plan.get('source_plan_id') and plan['source_plan_id']!=state['active_plan']['id']:
            raise ValueError('Source timetable changed; recalculate the economy proposal')
        errors=validate_plan(state,plan)
        if errors:
            raise ValueError({'message':'Plan is no longer feasible; recalculate','violations':errors})
        change=comparison(state,plan,basis='forecast_at_application')
        if not automatic:
            self.invalidate()
        state['active_plan']=copy.deepcopy(plan)
        state['awaiting_plan']=False
        self.sim.plans.clear()
        self.status(status='applied',comparison=change,applied_plan_id=plan['id'],automatic=automatic,
                    message=None,finished_at=datetime.now(timezone.utc).isoformat())
        self.emit('plan.applied',{'plan_id':plan['id'],'automatic':automatic,'comparison':change})

    async def run(self):
        request=None
        try:
            while self.pending is not None and not self.closing:
                request=self.pending
                await asyncio.sleep(self.debounce)
                if self.pending is not request:
                    continue
                snapshot=copy.deepcopy(self.sim.state)
                attempt=self.sim.state['replan_status']['attempt']+1
                self.status(status='calculating',attempt=attempt,constraint_version=snapshot['constraint_version'],
                            calculated_from_s=snapshot['sim_time_s'])
                self.emit('replan.started',request)
                self.publish()
                began=time.perf_counter()
                try:
                    result=await self.solve(snapshot)
                except Exception:
                    logger.exception('Replanning failed')
                    result={'plans':[],'message':'Ошибка расчёта. Новые отправления остаются удержаны, если есть сбой.'}
                if self.closing:
                    break
                if self.pending is not request:
                    # Reset or a newer incident invalidated this entire calculation.
                    continue
                state=self.sim.state
                if snapshot['epoch']!=state['epoch']:
                    self.pending=None
                    break
                if snapshot['constraint_version']!=state['constraint_version']:
                    self.status(status='queued',attempt=0,message='Условия изменились; расчёт обновляется.')
                    continue
                elapsed=round(time.perf_counter()-began,3)
                plans=[]
                rejected=[]
                for plan in result['plans']:
                    errors=validate_plan(state,plan)
                    if errors:
                        rejected.extend(errors)
                        continue
                    plan.update(epoch=state['epoch'],constraint_version=state['constraint_version'],
                                calculation_s=elapsed,within_budget=elapsed<=5)
                    plans.append(plan)
                if not plans and rejected and all(e['code']=='past' for e in rejected) and attempt<3:
                    self.status(status='queued',message='Время модели изменилось; расчёт обновляется.')
                    continue
                if not plans:
                    self.pending=None
                    self.status(status='failed',elapsed_s=elapsed,message=result.get('message') or
                                'Допустимый актуальный план не найден. Устраните сбой или повторите расчёт.')
                    self.emit('replan.failed',{'job_id':request['job_id'],'message':state['replan_status']['message']})
                    continue
                for plan in plans:
                    self.sim.plans[plan['id']]=plan
                    self.save_plan(plan)
                preferred=state['replanning_options']['policy']=='passenger_priority'
                best=min(plans,key=lambda plan:objective_value(state,plan,preferred))
                self.status(status='review',elapsed_s=elapsed,message=None,recommended_plan_id=best['id'],
                            comparison=comparison(state,best),finished_at=datetime.now(timezone.utc).isoformat())
                self.emit('replan.completed',{'job_id':request['job_id'],'plans':plans,'elapsed_s':elapsed,'within_budget':elapsed<=5})
                self.pending=None
                if request['automatic']:
                    # No await between current-state validation and installation.
                    self.install(best,automatic=True)
        except Exception:
            logger.exception('Replanning result processing failed')
            if self.pending is request or (request and self.sim.state['replan_status'].get('job_id')==request['job_id']):
                self.pending=None
                self.sim.plans.clear()
                self.status(status='failed',message='Ошибка обработки плана. Действующий план сохранён; повторите расчёт.')
                self.emit('replan.failed',{'job_id':request['job_id'],'message':self.sim.state['replan_status']['message']})
        finally:
            self.sim.replanning=False
            if not self.closing:
                self.sim.state['state_version']+=1
                self.publish()

    async def close(self):
        self.closing=True
        self.invalidate()
        if self.task:
            await self.task
