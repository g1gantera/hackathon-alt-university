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
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator, ValidationError

from .domain import ROOT
from .metrics import DEFAULT_SETTINGS, metrics
from .planning import build_plans, warm_worker
from .simulator import Simulator
from .storage import Store
from .validation import validate_plan
from .railway_map import infrastructure
from .dispatch import dispatch_report
from .stage3_demo import demo_state, scenario_data, solve_demo
from .replanning import Replanner, comparison
from .timing import MAX_SPEED, UPDATE_INTERVAL_MS, ModelClock, run_periodic
from .speed_advice import eco_plan, train_profile
from .reports import csv_report
from .quality_config import Settings, canonical_settings
from backend.ingestion.client import Ingestion

logger = logging.getLogger('dispatch')
logging.basicConfig(level=logging.INFO,format='%(message)s')
sessions = {}
clients = set()
seq = 0
stream_id = str(uuid.uuid4())
last_saved_snapshot = None
sim = None
store = None
pool = None
replanner = None
demo_pool = None
demo_lock = None
eco_lock = None
demo_mode = os.environ.get('DEMO_MODE','true').lower()=='true'
ingestion = None


def require(request, role='dispatcher'):
    if demo_mode:
        return 'admin'
    session = sessions.get(request.cookies.get('dispatch_session'))
    if not session or session['expires']<time.time():
        raise HTTPException(401,'Please sign in')
    if {'viewer':0,'dispatcher':1,'admin':2}[session['role']] < {'viewer':0,'dispatcher':1,'admin':2}[role]:
        raise HTTPException(403,'Insufficient permissions')
    return session['role']


def realtime_metadata(sequence=None):
    return {'protocol_version':1,'stream_id':stream_id,'seq':seq if sequence is None else sequence,
            'observed_at':datetime.now(timezone.utc).isoformat(),'source':'simulation','update_interval_ms':UPDATE_INTERVAL_MS}


def live_snapshot(sequence=None):
    report=dispatch_report(sim.state,sim.state['active_plan'])
    snapshot=sim.snapshot(violations=report['conflicts'])
    snapshot['dispatch']={k:report[k] for k in ('valid','conflicts','policy','next_decisions')}
    return {**snapshot,'realtime':realtime_metadata(sequence)}


def event_envelope(kind,payload):
    return {**realtime_metadata(),'type':kind,'epoch':sim.state['epoch'],
            'sim_time_s':sim.state['sim_time_s'],'state_version':sim.state['state_version'],
            'plan_id':sim.state['active_plan']['id'],'payload':payload}


def emit(kind,payload):
    global seq
    seq += 1
    event=event_envelope(kind,payload)
    for queue in tuple(clients):
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)
    if kind not in ('state.updated','metrics.updated'):
        store.save('event',sim.state['epoch'],sim.state['sim_time_s'],event)
        logger.info(json.dumps({'event':kind,'seq':seq,'sim_time_s':sim.state['sim_time_s']}))


def publish():
    ingestion.deliver(live_snapshot())


def publish_normalized(snapshot):
    global last_saved_snapshot
    snapshot['realtime']=realtime_metadata(seq+1)
    key=(snapshot['epoch'],snapshot['state_version'])
    if key!=last_saved_snapshot:
        store.save('snapshot',snapshot['epoch'],snapshot['sim_time_s'],snapshot)
        last_saved_snapshot=key
    emit('state.updated',snapshot)
    emit('metrics.updated',snapshot['metrics'])


async def ticker():
    model_clock=ModelClock()

    def update():
        sim.tick(model_clock.step(epoch=sim.state['epoch'],speed=sim.state['speed'],
                                  running=sim.state['running'],held=sim.clock_held_for_replan))
        expired=[]
        for entry in sim.state['incidents']:
            if entry['end_s']<=sim.state['sim_time_s'] and 'resolved_s' not in entry and not entry.get('expiry_notified'):
                entry['expiry_notified']=True
                expired.append(entry)
                sim.state['state_version']+=1
                emit('incident.expired',entry)
        if expired and sim.state['awaiting_plan'] and not sim.replanning:
            queue_replan('expiry',sim.state['replanning_options']['auto_apply'])
        publish()
        store.maintain()

    await run_periodic(update)


async def solve_snapshot(snapshot):
    return await asyncio.get_running_loop().run_in_executor(pool,build_plans,snapshot)


async def solve_eco(snapshot,train_id):
    return await asyncio.get_running_loop().run_in_executor(pool,eco_plan,snapshot,train_id)


def queue_replan(trigger='manual',automatic=False):
    return replanner.request(trigger,automatic)


@asynccontextmanager
async def lifespan(app):
    global sim,store,pool,seq,stream_id,last_saved_snapshot,replanner,demo_pool,demo_lock,eco_lock,ingestion
    seq=0
    stream_id=str(uuid.uuid4())
    last_saved_snapshot=None
    clients.clear()
    sim=Simulator()
    store=Store()
    pool=ProcessPoolExecutor(max_workers=1)
    demo_pool=ProcessPoolExecutor(max_workers=1)
    demo_lock=asyncio.Lock()
    eco_lock=asyncio.Lock()
    ingestion=Ingestion(publish_normalized,lambda:(sim.state['epoch'],sim.state['state_version']))
    replanner=Replanner(sim,solve_snapshot,emit,publish,lambda plan:store.save('plan',plan['epoch'],sim.state['sim_time_s'],plan))
    task=None
    try:
        await asyncio.get_running_loop().run_in_executor(pool,warm_worker,sim.state)
        await ingestion.start(live_snapshot())
        task=asyncio.create_task(ticker())
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await replanner.close()
        await ingestion.close()
        pool.shutdown(wait=True,cancel_futures=True)
        demo_pool.shutdown(wait=True,cancel_futures=True)
        store.engine.dispose()



app=FastAPI(title='KTZH | Astana–Kokshetau dispatcher',version='0.1.0',lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1000)


@app.get('/api/health')
async def health():
    ready=ingestion is not None and ingestion.status['status']=='ready' and time.time()-ingestion.status.get('last_success_at',0)<5
    return {'status':'ready' if ready else 'degraded','ingestion':ingestion.status if ingestion else None,
            'history':store.statistics() if store else None}


class Login(BaseModel):
    role: Literal['viewer','dispatcher','admin']
    password: str


class Speed(BaseModel):
    multiplier: int=Field(gt=0,le=MAX_SPEED,strict=True)


class DemoRequest(BaseModel):
    scenario: Literal['opposing','following','fleet']='opposing'
    policy: Literal['balanced','passenger_priority']='balanced'
    priorities: dict[str,int]=Field(default_factory=dict,max_length=8)

    @model_validator(mode='after')
    def valid_priorities(self):
        if any(not 1<=value<=10 for value in self.priorities.values()):
            raise ValueError('Priorities must be from 1 to 10')
        return self


class Incident(BaseModel):
    kind: Literal['delay','closure','signal']
    target_id: str
    duration_s: int=Field(600,ge=30,le=7200)


class ReplanningOptions(BaseModel):
    auto_apply: bool=True
    policy: Literal['balanced','passenger_priority']='balanced'


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
    return live_snapshot()


@app.get('/api/trains')
async def trains(request:Request):
    require(request,'viewer')
    snapshot=live_snapshot()
    return {key:snapshot[key] for key in ('realtime','epoch','state_version','sim_time_s','trains')}


@app.get('/api/quality')
async def movement_quality(request:Request):
    require(request,'viewer')
    snapshot=live_snapshot()
    actual=snapshot['metrics']
    forecast=metrics(sim.state,sim.state['active_plan']) if snapshot['dispatch']['valid'] and not snapshot['awaiting_plan'] else None
    now=snapshot['sim_time_s']
    trend=store.quality_history(snapshot['epoch'],max(0,now-900),now,actual['quality_signature'])
    current={'sim_time_s':now,'state_version':snapshot['state_version'],'index':actual['index']}
    if not trend or trend[-1]!=current:
        trend.append(current)
    return {'epoch':snapshot['epoch'],'state_version':snapshot['state_version'],'sim_time_s':now,
            'constraint_version':snapshot['constraint_version'],'active_plan_id':snapshot['active_plan_id'],
            'realtime':snapshot['realtime'],'actual':actual,'forecast':forecast,
            'trend':{'window_start_s':max(0,now-900),'window_end_s':now,'quality_signature':actual['quality_signature'],
                     'points':trend,'sample_count':len(trend)}}


@app.get('/api/trains/{train_id}')
async def train_state(train_id:str,request:Request):
    require(request,'viewer')
    snapshot=live_snapshot()
    train=next((t for t in snapshot['trains'] if t['id']==train_id),None)
    if train is None:
        raise HTTPException(404,'Unknown train')
    return {**{key:snapshot[key] for key in ('realtime','epoch','state_version','sim_time_s')},'train':train}


@app.get('/api/topology')
async def get_topology(request:Request):
    require(request,'viewer')
    return sim.state['topology']


@app.get('/api/network')
async def network(request:Request):
    require(request,'viewer')
    return FileResponse(ROOT/'data/kazakhstan_railways.geojson',media_type='application/geo+json')


@app.get('/api/map')
async def map_infrastructure(request:Request):
    require(request,'viewer')
    return infrastructure(sim.state['topology'])


@app.post('/api/simulation/{action}')
async def control(action:str,request:Request):
    require(request)
    if action=='start':
        if not sim.state['awaiting_plan']:
            errors=validate_plan(sim.state,sim.state['active_plan'])
            if errors:
                raise HTTPException(409,{'message':'Active schedule is invalid; calculate and apply a new plan','violations':errors})
        sim.state['running']=True
    elif action=='pause':
        sim.state['running']=False
    elif action=='reset':
        replanner.invalidate()
        sim.reset()
    elif action=='speed':
        try:
            body=Speed.model_validate(await request.json())
        except (ValidationError,ValueError):
            raise HTTPException(422,f'Speed must be a whole number from 1 to {MAX_SPEED}')
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
    queue_replan('incident',sim.state['replanning_options']['auto_apply'])
    return entry


@app.post('/api/incidents/{incident_id}/resolve')
async def resolve_incident(incident_id:str,request:Request):
    require(request)
    entry=next((i for i in sim.state['incidents'] if i['id']==incident_id),None)
    if entry is None:
        raise HTTPException(404,'Unknown incident')
    now=sim.state['sim_time_s']
    if entry['end_s']<=now or 'resolved_s' in entry:
        return entry
    entry['scheduled_end_s']=entry['end_s']
    entry['end_s']=now
    entry['resolved_s']=now
    sim.state['constraint_version']+=1
    sim.state['state_version']+=1
    sim.state['awaiting_plan']=True
    sim.plans.clear()
    emit('incident.resolved',entry)
    queue_replan('resolved',sim.state['replanning_options']['auto_apply'])
    return entry


@app.get('/api/replanning')
async def replanning_status(request:Request):
    require(request,'viewer')
    return {'options':sim.state['replanning_options'],'status':sim.state['replan_status']}


@app.put('/api/replanning')
async def replanning_options(body:ReplanningOptions,request:Request):
    require(request)
    sim.state['replanning_options']=body.model_dump()
    sim.state['state_version']+=1
    emit('replan.options_changed',body.model_dump())
    if sim.state['awaiting_plan'] or sim.replanning:
        queue_replan('options',body.auto_apply)
    else:
        publish()
    return body.model_dump()


@app.post('/api/replanning/retry')
async def retry_replanning(request:Request):
    require(request)
    return queue_replan('retry',sim.state['replanning_options']['auto_apply'])


@app.post('/api/replan')
async def replan(request:Request):
    require(request)
    return queue_replan()


@app.get('/api/plans')
async def plans(request:Request):
    require(request,'viewer')
    return {'plans':list(sim.plans.values()),'active':sim.state['active_plan'],'baseline':sim.state['baseline']}


@app.get('/api/dispatch')
async def dispatch_status(request:Request,plan_id:str|None=None):
    require(request,'viewer')
    plan=sim.state['active_plan'] if plan_id is None or plan_id==sim.state['active_plan']['id'] else sim.plans.get(plan_id)
    if plan is None:
        raise HTTPException(404,'Plan not found; recalculate')
    report=dispatch_report(sim.state,plan)
    report['active']=plan['id']==sim.state['active_plan']['id']
    report['applicable']=not report['active'] and report['valid'] and plan.get('epoch')==sim.state['epoch'] and plan.get('constraint_version')==sim.state['constraint_version']
    return report


@app.get('/api/plans/{plan_id}/comparison')
async def compare_plan(plan_id:str,request:Request):
    require(request,'viewer')
    plan=sim.plans.get(plan_id)
    if plan is None:
        raise HTTPException(404,'Plan not found; recalculate')
    state=sim.state
    if plan.get('epoch')!=state['epoch'] or plan.get('constraint_version')!=state['constraint_version']:
        raise HTTPException(409,'Plan conditions changed; recalculate')
    if plan.get('source_plan_id') and plan['source_plan_id']!=state['active_plan']['id']:
        raise HTTPException(409,'Source timetable changed; recalculate')
    change=comparison(state,plan)
    return {'epoch':state['epoch'],'constraint_version':state['constraint_version'],
            'active_plan_id':state['active_plan']['id'],'plan_id':plan_id,'sim_time_s':state['sim_time_s'],
            'applicable':change['after']['forecast_valid'],'comparison':change}


@app.get('/api/stage3/scenario')
async def stage3_scenario(request:Request,scenario:Literal['opposing','following','fleet']='opposing'):
    require(request,'viewer')
    return scenario_data(demo_state(scenario))


@app.post('/api/stage3/solve')
async def stage3_solve(body:DemoRequest,request:Request):
    require(request,'viewer')
    try:
        demo_state(body.scenario,body.priorities)
    except ValueError as error:
        raise HTTPException(422,str(error))
    if demo_lock.locked():
        raise HTTPException(409,'The demo is calculating. Please try again shortly.')
    async with demo_lock:
        return await asyncio.get_running_loop().run_in_executor(demo_pool,solve_demo,body.scenario,body.priorities,body.policy)


@app.post('/api/plans/{plan_id}/apply')
async def apply(plan_id:str,request:Request):
    require(request)
    plan=sim.plans.get(plan_id)
    if not plan:
        raise HTTPException(404,'Plan not found; recalculate')
    try:
        replanner.install(plan)
    except ValueError as error:
        raise HTTPException(409,error.args[0])
    publish()
    return sim.snapshot()


@app.get('/api/trains/{train_id}/profile')
async def speed_profile(train_id:str,request:Request):
    require(request,'viewer')
    train=next((t for t in sim.state['fleet'] if t['id']==train_id),None)
    if not train:
        raise HTTPException(404,'Unknown train')
    return train_profile(sim.state,train)


@app.get('/api/trains/{train_id}/advice')
async def speed_advice(train_id:str,request:Request):
    require(request,'viewer')
    snapshot=live_snapshot()
    train=next((t for t in snapshot['trains'] if t['id']==train_id),None)
    if train is None:
        raise HTTPException(404,'Unknown train')
    return {'epoch':snapshot['epoch'],'state_version':snapshot['state_version'],'realtime':snapshot['realtime'],
            'advice':train['speed_advice'],'profile':train_profile(sim.state,train)}


@app.post('/api/trains/{train_id}/eco-plan')
async def economy_proposal(train_id:str,request:Request):
    require(request)
    if train_id not in [t['id'] for t in sim.state['fleet']]:
        raise HTTPException(404,'Unknown train')
    if sim.state['running'] or sim.state['awaiting_plan'] or sim.replanning:
        raise HTTPException(409,'Pause the model and wait for a valid active timetable first')
    if eco_lock.locked():
        raise HTTPException(409,'An economy proposal is already being calculated')
    async with eco_lock:
        snapshot=copy.deepcopy(sim.state)
        result=await solve_eco(snapshot,train_id)
        current=sim.state
        # Neither an incident, reset, new timetable nor a start/pause cycle may
        # let an obsolete worker result replace the current speed recommendation.
        if any(current[key]!=snapshot[key] for key in ('epoch','constraint_version','sim_time_s','committed')) or current['active_plan']['id']!=snapshot['active_plan']['id'] or current['running'] or current['awaiting_plan'] or sim.replanning:
            raise HTTPException(409,'The scenario changed during calculation; try again')
        plan=result.get('plan')
        if plan:
            if validate_plan(current,plan):
                raise HTTPException(409,'The economy proposal is no longer feasible')
            # Retain at most one economy proposal per train, plus dispatch variants.
            sim.plans={key:p for key,p in sim.plans.items() if p.get('eco',{}).get('train_id')!=train_id}
            sim.plans[plan['id']]=plan
            store.save('plan',current['epoch'],current['sim_time_s'],plan)
            emit('eco.plan_ready',{'train_id':train_id,'plan_id':plan['id']})
        return {**result,'epoch':current['epoch'],'base_plan_id':current['active_plan']['id'],
                'constraint_version':current['constraint_version'],'sim_time_s':current['sim_time_s']}


@app.get('/api/history')
async def history(request:Request,start:float=Query(0,alias='from',ge=0),end:float=Query(1e10,alias='to',ge=0)):
    require(request,'viewer')
    now=sim.state['sim_time_s']
    return store.history(sim.state['epoch'],max(start,now-900),min(end,now))


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


@app.get('/api/history/runs')
def history_runs(request:Request):
    require(request,'viewer')
    return {'current_epoch':sim.state['epoch'],'retention_hours':store.retention_hours,'runs':store.runs()}


@app.get('/api/history/window')
def history_window(request:Request,epoch:str|None=None,minutes:int=Query(15,ge=5,le=15,multiple_of=5),
                   end:float|None=Query(None,alias='to',ge=0,allow_inf_nan=False)):
    require(request,'viewer')
    window=store.archive_window(epoch or sim.state['epoch'],minutes,end)
    if window is None:
        raise HTTPException(404,'Saved run not found or its retention period has expired')
    return window


@app.get('/api/history/snapshots/{record_id}')
def history_snapshot(record_id:int,request:Request,epoch:str):
    require(request,'viewer')
    snapshot=store.archive_snapshot(epoch,record_id)
    if snapshot is None:
        raise HTTPException(404,'Snapshot not found or its retention period has expired')
    return snapshot


@app.get('/api/reports/history.csv')
def history_report(request:Request,epoch:str,minutes:int=Query(15,ge=5,le=15,multiple_of=5),
                   end:float=Query(...,alias='to',ge=0,allow_inf_nan=False),
                   through_id:int=Query(...,gt=0)):
    require(request,'viewer')
    window=store.archive_window(epoch,minutes,end,through_id)
    if window is None or not window['frames']:
        raise HTTPException(404,'No saved snapshots in this report window')
    filename=f"railflow-report-{int(window['from_s'])}-{int(window['to_s'])}.csv"
    return StreamingResponse(csv_report(store,window),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename={filename}','Cache-Control':'no-store'})


@app.get('/api/settings')
async def settings(request:Request):
    require(request,'viewer')
    return canonical_settings(sim.state['settings'])


@app.put('/api/settings')
async def update_settings(body:Settings,request:Request):
    require(request,'admin')
    sim.state['settings']=canonical_settings(body.model_dump())
    sim.state['constraint_version']+=1
    sim.state['state_version']+=1
    sim.state['active_plan']['metrics']=metrics(sim.state,sim.state['active_plan'])
    sim.state['baseline']['metrics']=metrics(sim.state,sim.state['baseline'])
    sim.plans.clear()
    emit('settings.updated',sim.state['settings'])
    if sim.state['awaiting_plan'] or sim.replanning:
        queue_replan('settings',sim.state['replanning_options']['auto_apply'])
    elif sim.state['replan_status']['status']=='review':
        sim.state['replan_status']={'status':'idle','comparison':None,'message':None}
    publish()
    return sim.state['settings']


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
    try:
        initial=await ingestion.current_snapshot()
    except asyncio.TimeoutError:
        await ws.close(code=1013)
        return
    queue=asyncio.Queue(maxsize=6)
    clients.add(queue)
    async def receive():
        while True:
            await ws.receive_text()
    receiver=asyncio.create_task(receive())
    try:
        initial['realtime']=realtime_metadata()
        await asyncio.wait_for(ws.send_json(event_envelope('state.updated',initial)),timeout=2)
        while not receiver.done():
            try:
                require(ws,'viewer')
            except HTTPException:
                await ws.close(code=1008)
                break
            try:
                event=await asyncio.wait_for(queue.get(),timeout=2)
            except asyncio.TimeoutError:
                continue
            await asyncio.wait_for(ws.send_json(event),timeout=2)
    except (WebSocketDisconnect,RuntimeError,asyncio.TimeoutError):
        pass
    finally:
        clients.discard(queue)
        receiver.cancel()
        with suppress(asyncio.CancelledError,WebSocketDisconnect):
            await receiver


if (ROOT/'frontend/dist').exists():
    app.mount('/',StaticFiles(directory=ROOT/'frontend/dist',html=True),name='frontend')
