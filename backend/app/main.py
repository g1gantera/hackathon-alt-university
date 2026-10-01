import asyncio
import copy
import csv
import io
import json
import logging
import os
import secrets
import time
import uuid
from collections import deque, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator, ValidationError

from .domain import ROOT, movement_profile
from .demo_metrics import DEFAULT_SETTINGS, metrics
from .integration import build_plans, warm_worker, validate_plan, LogicSimulator, public_plan, compact_state, INCIDENTS
from .simulator import Simulator
from .storage import Store
from .ingestion_client import IngestionClient

logger = logging.getLogger('dispatch')
logging.basicConfig(level=logging.INFO,format='%(message)s')
sessions = {}
clients = set()
seq = 0
last_saved_snapshot = None
sim = None
store = None
pool = None
job_task = None
ingestion = None
demo_mode = os.environ.get('DEMO_MODE','false').lower()=='true'


performance_events = OrderedDict()
paint_samples = deque(maxlen=2000)
state_times = deque(maxlen=2000)


def require(request, role='dispatcher'):
    if demo_mode:
        return 'admin'
    session = sessions.get(request.cookies.get('dispatch_session'))
    if not session or session['expires']<time.time():
        raise HTTPException(401,'Please sign in')
    if {'viewer':0,'dispatcher':1,'admin':2}[session['role']] < {'viewer':0,'dispatcher':1,'admin':2}[role]:
        raise HTTPException(403,'Insufficient permissions')
    return session['role']


def emit(kind,payload,observed_at=None):
    global seq
    seq += 1
    performance_events[seq] = observed_at or time.perf_counter()
    if len(performance_events) > 4000:
        performance_events.popitem(last=False)
    if kind == 'state.updated':
        state_times.append(time.perf_counter())
    event={'type':kind,'seq':seq,'sim_time_s':sim.state['sim_time_s'],
           'state_version':sim.state['state_version'],'plan_id':sim.state['active_plan']['id'],'payload':payload}
    for queue in tuple(clients):
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)
    if kind not in ('state.updated','metrics.updated'):
        store.save('event',sim.state['epoch'],sim.state['sim_time_s'],event)
        logger.info(json.dumps({'event':kind,'seq':seq,'sim_time_s':sim.state['sim_time_s']}))


def publish():
    global last_saved_snapshot
    observed_at=time.perf_counter()
    snapshot=sim.snapshot()
    key=(sim.state['epoch'],sim.state['state_version'])
    if key!=last_saved_snapshot:
        store.save('snapshot',sim.state['epoch'],sim.state['sim_time_s'],snapshot)
        last_saved_snapshot=key
    if ingestion is not None:
        ingestion.publish(snapshot,observed_at)
    else:
        deliver_snapshot(snapshot)


def deliver_snapshot(snapshot,observed_at=None):
    # An old service response must not roll the UI back after reset or an incident.
    if snapshot['epoch'] != sim.state['epoch'] or snapshot['state_version'] < sim.state['state_version']:
        return
    emit('state.updated',snapshot,observed_at)
    emit('metrics.updated',snapshot['metrics'])


async def ticker():
    deadline = time.monotonic()
    previous = deadline
    while True:
        deadline += 0.5  # 2 Hz leaves headroom above the required 1 Hz
        await asyncio.sleep(max(0, deadline - time.monotonic()))
        now = time.monotonic()
        sim.tick((now - previous) * sim.state['speed'])
        previous = now
        if now - deadline > 0.5:
            deadline = now
        publish()


async def calculate(job_id, snapshot):
    began=time.perf_counter()
    superseded=False
    sim.replanning=True
    emit('replan.started',{'job_id':job_id})
    try:
        result=await asyncio.get_running_loop().run_in_executor(pool,build_plans,snapshot)
        result['elapsed_s']=round(time.perf_counter()-began,3)
        result['within_budget']=result['elapsed_s']<=5
        if snapshot['epoch']!=sim.state['epoch'] or snapshot['constraint_version']!=sim.state['constraint_version']:
            superseded=snapshot['epoch']==sim.state['epoch']
            emit('replan.failed',{'job_id':job_id,'message':'Условия изменились. Расчёт обновляется.'})
            return
        for plan in result['plans']:
            plan['calculation_s']=result['elapsed_s']
            plan['within_budget']=result['within_budget']
            plan['epoch']=snapshot['epoch']
            plan['constraint_version']=snapshot['constraint_version']
            sim.plans[plan['id']]=plan
            store.save('plan',snapshot['epoch'],snapshot['sim_time_s'],public_plan(plan) | {'native': plan.get('_native')})
        result['elapsed_s']=round(time.perf_counter()-began,3)
        result['within_budget']=result['elapsed_s']<=5
        for plan in result['plans']:
            plan['calculation_s']=result['elapsed_s']
            plan['within_budget']=result['within_budget']
        emit('replan.completed' if result['plans'] else 'replan.failed',{'job_id':job_id,**result,'plans':[public_plan(p) for p in result['plans']]})
    except Exception:
        logger.exception('Planning failed')
        emit('replan.failed',{'job_id':job_id,'message':'Ошибка расчёта; действующий план не заменён.'})
    finally:
        sim.replanning=False
        publish()
        if superseded:
            # Schedule after this task completes so queue_replan sees an idle worker.
            asyncio.get_running_loop().call_soon(queue_replan)


def queue_replan():
    global job_task
    if job_task and not job_task.done():
        return {'job_id':'running','status':'running'}
    job_id=str(uuid.uuid4())
    if isinstance(sim,LogicSimulator):
        sim.replanning=True
    job_task=asyncio.create_task(calculate(job_id,compact_state(sim.state) if isinstance(sim,LogicSimulator) else copy.deepcopy(sim.state)))
    return {'job_id':job_id,'status':'queued'}


@asynccontextmanager
async def lifespan(app):
    global sim,store,pool,ingestion,last_saved_snapshot,job_task
    sim=Simulator() if os.environ.get('DISPATCH_ENGINE','logic')=='demo' else LogicSimulator()
    store=Store()
    last_saved_snapshot=None
    job_task=None
    performance_events.clear()
    paint_samples.clear()
    state_times.clear()
    ingestion=IngestionClient(deliver_snapshot)
    pool=ProcessPoolExecutor(max_workers=1)
    await asyncio.get_running_loop().run_in_executor(pool,warm_worker,sim.state)
    publish()
    task=asyncio.create_task(ticker())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    if job_task and not job_task.done():
        await job_task
    await ingestion.close()
    ingestion=None
    pool.shutdown(wait=True,cancel_futures=True)
    await asyncio.to_thread(store.close)


app=FastAPI(title='KTZH | Astana–Kokshetau dispatcher',version='0.1.0',lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1000)


class Login(BaseModel):
    role: Literal['viewer','dispatcher','admin']
    password: str


class Speed(BaseModel):
    multiplier: Literal[1,5,15,30,60]


class Incident(BaseModel):
    kind: Literal['delay','closure','signal','speed_restriction']
    target_id: str
    duration_s: int=Field(600,ge=30,le=7200)


class Settings(BaseModel):
    passenger_weight: float=Field(3,gt=0,le=20)
    freight_weight: float=Field(1,gt=0,le=20)
    delay_weight: float=Field(.7,ge=0,le=1)
    energy_weight: float=Field(.3,ge=0,le=1)
    delay_norm_s: float=Field(7200,gt=0,le=1000000)
    energy_norm_kwh: float=Field(100000,gt=0,le=10000000)
    arrival_tolerance_s: float=Field(300,ge=0,le=3600)

    @model_validator(mode='after')
    def weights(self):
        if self.delay_weight+self.energy_weight<=0:
            raise ValueError('At least one metric weight must be positive')
        return self


@app.post('/api/auth/login')
async def login(body:Login,response:Response):
    configured=os.environ.get(body.role.upper()+'_PASSWORD','')
    if not configured or not secrets.compare_digest(body.password,configured):
        raise HTTPException(401,'Invalid credentials')
    token=secrets.token_urlsafe(32)
    sessions[token]={'role':body.role,'expires':time.time()+8*3600}
    response.set_cookie('dispatch_session',token,httponly=True,samesite='strict',secure=os.environ.get('COOKIE_SECURE','false')=='true',max_age=8*3600)
    return {'role':body.role}


@app.get('/api/auth/me')
async def me(request:Request):
    return {'role':require(request,'viewer'),'demo':demo_mode}


@app.post('/api/auth/logout')
async def logout(request:Request,response:Response):
    sessions.pop(request.cookies.get('dispatch_session'),None)
    response.delete_cookie('dispatch_session')
    return {'ok':True}


@app.get('/api/state')
async def state(request:Request):
    require(request,'viewer')
    return sim.snapshot()


@app.get('/api/topology')
async def get_topology(request:Request):
    require(request,'viewer')
    return sim.state['topology']


@app.get('/api/network')
async def network(request:Request):
    require(request,'viewer')
    return FileResponse(ROOT/'data/kazakhstan_railways.geojson',media_type='application/geo+json')


@app.post('/api/simulation/{action}')
async def control(action:str,request:Request):
    require(request)
    if action=='start':
        sim.state['running']=True
    elif action=='pause':
        sim.state['running']=False
    elif action=='reset':
        if sim.replanning:
            raise HTTPException(409,'Wait until calculation completes before reset')
        sim.replanning=True
        try:
            fresh=await asyncio.to_thread(type(sim))
            sim.__dict__.update(fresh.__dict__)
        finally:
            sim.replanning=False
    elif action=='speed':
        try:
            body=Speed.model_validate(await request.json())
        except (ValidationError,ValueError):
            raise HTTPException(422,'Speed must be one of 1, 5, 15, 30, 60')
        sim.state['speed']=body.multiplier
    else:
        raise HTTPException(404,'Unknown action')
    sim.state['state_version']+=1
    emit('simulation.changed',{'action':action})
    publish()
    return sim.snapshot()


@app.post('/api/incidents')
async def incident(body:Incident,request:Request):
    require(request)
    if isinstance(sim,LogicSimulator):
        if sim.replanning:
            raise HTTPException(409,'Wait until calculation completes')
        try:
            entry=sim.add_incident(body.kind,body.target_id,body.duration_s)
        except KeyError:
            raise HTTPException(404,'Unknown section')
        except ValueError as error:
            raise HTTPException(409,str(error))
        emit('incident.created',entry)
        publish()
        queue_replan()
        return entry
    if body.kind=='speed_restriction':
        raise HTTPException(422,'Speed restriction requires the logic engine')
    leg=None
    if body.kind=='delay':
        train=next((t for t in sim.snapshot()['trains'] if t['id']==body.target_id),None)
        if not train or train['status']!='waiting':
            raise HTTPException(409,'Choose a train waiting at a station')
        leg=train['next_leg']
    elif body.target_id not in [s['id'] for s in sim.state['topology']['sections']]:
        raise HTTPException(404,'Unknown section')
    now=sim.state['sim_time_s']
    entry={**body.model_dump(),'id':str(uuid.uuid4()),'leg':leg,'start_s':now,'created_s':now,'end_s':now+body.duration_s}
    sim.state['incidents'].append(entry)
    sim.state['state_version']+=1
    sim.state['constraint_version']+=1
    sim.state['awaiting_plan']=True
    sim.plans.clear()
    emit('incident.created',entry)
    publish()
    # Coalesce rapid incidents, then compute the latest snapshot.
    async def deferred():
        await asyncio.sleep(.3)
        if job_task and not job_task.done():
            await asyncio.shield(job_task)
        if sim.state['awaiting_plan'] and not sim.plans:
            queue_replan()
    asyncio.create_task(deferred())
    return entry


class IncidentBatch(BaseModel):
    incidents: list[Incident] = Field(min_length=1, max_length=10)


@app.post('/api/incidents/batch')
async def incident_batch(body:IncidentBatch, request:Request):
    require(request)
    if not isinstance(sim,LogicSimulator):
        raise HTTPException(409,'Batch incidents require the logic engine')
    if sim.replanning:
        raise HTTPException(409,'Wait until calculation completes')
    candidate=copy.copy(sim)
    candidate.state=compact_state(sim.state)
    for key in ('active_plan','baseline'):
        candidate.state[key]['_profiles']=sim.state[key]['_profiles']
    candidate.plans={}
    try:
        entries=[candidate.add_incident(i.kind,i.target_id,i.duration_s) for i in body.incidents]
    except KeyError:
        raise HTTPException(404,'Unknown incident target; no incidents applied')
    except ValueError as error:
        raise HTTPException(409,str(error))
    sim.state=candidate.state
    sim.plans.clear()
    sim._validation_cache=None
    for entry in entries:
        emit('incident.created',entry)
    publish()
    return {'incidents':entries, **queue_replan()}


@app.post('/api/replan')
async def replan(request:Request):
    require(request)
    return queue_replan()


@app.get('/api/plans')
async def plans(request:Request):
    require(request,'viewer')
    return {'plans':[public_plan(p) for p in sim.plans.values()],'active':sim.active_public_plan() if isinstance(sim,LogicSimulator) else public_plan(sim.state['active_plan']),'baseline':public_plan(sim.state['baseline'])}


@app.post('/api/plans/{plan_id}/apply')
async def apply(plan_id:str,request:Request):
    require(request)
    plan=sim.plans.get(plan_id)
    if not plan:
        raise HTTPException(404,'Plan not found; recalculate')
    if plan['epoch']!=sim.state['epoch'] or plan['constraint_version']!=sim.state['constraint_version']:
        raise HTTPException(409,'Plan conditions changed; recalculate')
    errors=validate_plan(sim.state,plan)
    if errors:
        raise HTTPException(409,{'message':'Plan is no longer feasible; recalculate','violations':errors})
    sim.state['active_plan']=plan
    sim.state['awaiting_plan']=False
    sim.state['state_version']+=1
    sim.plans.clear()
    emit('plan.applied',{'plan_id':plan_id})
    publish()
    return sim.snapshot()


@app.get('/api/trains/{train_id}/profile')
async def speed_profile(train_id:str,request:Request):
    require(request,'viewer')
    if isinstance(sim,LogicSimulator):
        try:
            return sim.profile(train_id)
        except KeyError:
            raise HTTPException(404,'Unknown train')
    train=next((t for t in sim.state['fleet'] if t['id']==train_id),None)
    if not train:
        raise HTTPException(404,'Unknown train')
    points=[]
    energy=0
    position=0
    legs=sorted([m for m in sim.state['active_plan']['movements'] if m['train_id']==train_id],key=lambda m:m['leg'])
    for m in legs:
        section=next(s for s in sim.state['topology']['sections'] if s['id']==m['section_id'])
        p=movement_profile(train,section,m['end_s']-m['start_s'])
        points.extend([[m['start_s']+t,position+x,v,p['limit_mps'],energy+e] for t,x,v,e in p['points']])
        energy+=p['energy_kwh']
        position+=section['length_m']
    return {'train_id':train_id,'plan_id':sim.state['active_plan']['id'],'points':points,'energy_kwh':energy,
            'arrival_s':legs[-1]['end_s'],'reachable':True,'assumptions':p['assumptions']}


@app.get('/api/history')
async def history(request:Request,start:float=Query(0,alias='from',ge=0),end:float=Query(1e10,alias='to',ge=0)):
    require(request,'viewer')
    now=sim.state['sim_time_s']
    await asyncio.to_thread(store.flush)
    return await asyncio.to_thread(store.history,sim.state['epoch'],max(start,now-900),min(end,now))


@app.get('/api/report.csv')
async def report(request:Request):
    require(request,'viewer')
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(['sim_time_s','train_id','type','position_m','speed_mps','actual_delay_s','actual_energy_kwh','status'])
    snap=sim.snapshot()
    for t in snap['trains']:
        writer.writerow([snap['sim_time_s'],t['id'],t['type'],round(t['position_m'],2),round(t['speed_mps'],2),t['delay_s'],round(t['energy_kwh'],2),t['status']])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=dispatch-report.csv'})


@app.get('/api/settings')
async def settings(request:Request):
    require(request,'viewer')
    return sim.state['settings']


@app.put('/api/settings')
async def update_settings(body:Settings,request:Request):
    require(request,'admin')
    if sim.replanning:
        raise HTTPException(409,'Wait until calculation completes')
    if isinstance(sim,LogicSimulator):
        sim.update_settings(body.model_dump())
    else:
        sim.state['settings']=body.model_dump()
        sim.state['constraint_version']+=1
        sim.state['state_version']+=1
        sim.state['active_plan']['metrics']=metrics(sim.state,sim.state['active_plan'])
        sim.state['baseline']['metrics']=metrics(sim.state,sim.state['baseline'])
    sim.plans.clear()
    emit('settings.updated',sim.state['settings'])
    publish()
    return sim.state['settings']


@app.get('/api/logic/scenarios')
async def logic_cases(request:Request):
    require(request,'viewer')
    return {'engine':sim.state.get('engine','demo'),'scenarios':list(INCIDENTS)}


@app.post('/api/logic/scenarios/{kind}')
async def load_logic_case(kind:str,request:Request):
    require(request)
    if not isinstance(sim,LogicSimulator):
        raise HTTPException(409,'Select DISPATCH_ENGINE=logic and restart')
    if kind not in INCIDENTS:
        raise HTTPException(404,'Unknown scenario')
    if sim.replanning:
        raise HTTPException(409,'Wait until calculation completes')
    sim.replanning=True
    try:
        def prepare_case():
            candidate=copy.copy(sim)
            candidate.load_case(kind)
            return candidate
        candidate=await asyncio.to_thread(prepare_case)
        sim.__dict__.update(candidate.__dict__)
    finally:
        sim.replanning=False
    emit('simulation.changed',{'action':'scenario','kind':kind})
    publish()
    queue_replan()
    return sim.snapshot()


@app.get('/api/logic/diagnostics')
async def logic_diagnostics(request:Request):
    require(request,'viewer')
    if not isinstance(sim,LogicSimulator):
        raise HTTPException(409,'Logic engine is not active')
    from .logic_api import diagnose
    return await asyncio.to_thread(diagnose,compact_state(sim.state))


@app.websocket('/ws')
async def websocket(ws:WebSocket):
    origin=ws.headers.get('origin')
    if origin and origin.split('://')[-1]!=ws.headers.get('host'):
        await ws.close(code=1008)
        return
    try:
        require(ws,'viewer')
    except HTTPException:
        await ws.close(code=1008)
        return
    await ws.accept()
    queue=asyncio.Queue(maxsize=6)
    clients.add(queue)
    await ws.send_json({'type':'state.updated','seq':seq,'sim_time_s':sim.state['sim_time_s'],
                        'state_version':sim.state['state_version'],'plan_id':sim.state['active_plan']['id'],
                        'payload':sim.snapshot()})
    async def receive():
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get('type') == 'paint.ack':
                event_seq = data.get('seq')
                started = performance_events.get(event_seq) if isinstance(event_seq, int) else None
                if started is not None:
                    elapsed = (time.perf_counter() - started) * 1000
                    render = data.get('render_ms')
                    if isinstance(render, (int, float)) and 0 <= render <= 60_000:
                        paint_samples.append({'seq':event_seq,'event_to_ack_ms':round(elapsed,2),
                                              'client_render_ms':round(render,2)})
    receiver=asyncio.create_task(receive())
    try:
        while not receiver.done():
            try:
                event=await asyncio.wait_for(queue.get(),timeout=2)
                await asyncio.wait_for(ws.send_json(event),timeout=2)
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect,RuntimeError):
        pass
    finally:
        clients.discard(queue)
        receiver.cancel()
        with suppress(asyncio.CancelledError,WebSocketDisconnect):
            await receiver


from .logic_api import router as logic_router

app.include_router(logic_router)

if (ROOT/'frontend/dist').exists():
    app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
