"""FastAPI HTTP commands + 2 Hz SSE snapshots. Run a single authoritative worker."""
import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .engine import Engine
from .history import EventBus, History
from .models import Control, DemoCommand, IncidentSpec, IngestEvent, Settings, Strict, TrainSpec, TrainUpdate
from .network import Network, ROOT

DATA=Path(os.getenv('RAIL_DATA_DIR',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
engine=Engine(Network(),History(DATA/'history.sqlite3'))
bus=EventBus()
sessions={}
idempotency={}
idempotency_lock=asyncio.Lock()
login_attempts={}
worker_metrics={'tick_ms':0,'max_tick_ms':0,'lag_s':0,'connections':0}


def credentials():
    return {'dispatcher':os.getenv('RAIL_DISPATCHER_PASSWORD','demo-dispatch'),'admin':os.getenv('RAIL_ADMIN_PASSWORD','demo-admin')}


async def actor(request:Request):
    token=request.cookies.get('rail_session','')
    entry=sessions.get(token)
    if entry and entry['expires']>time.time():
        return entry['user']
    header=request.headers.get('authorization','')
    if header.startswith('Basic '):
        try:
            user,password=base64.b64decode(header[6:],validate=True).decode().split(':',1)
            expected=credentials().get(user)
            if expected and hmac.compare_digest(password,expected):
                return user
        except (ValueError,UnicodeDecodeError):
            pass
    # This is also a cookie-session API. A Basic challenge on the startup /me
    # fetch opens a browser-native modal and suspends page timers/iframe scripts.
    # Explicit HTTP Basic credentials are still accepted above.
    raise HTTPException(401,'Sign in to the demonstration')


async def admin(user=Depends(actor)):
    if user!='admin':
        raise HTTPException(403,'Administrator access is required for settings')
    return user


async def clock_loop():
    previous=time.monotonic(); last_publish=previous; pending=0
    while True:
        await asyncio.sleep(.05)
        now=time.monotonic(); elapsed=now-previous; previous=now
        start=time.perf_counter()
        if engine.running:
            pending+=elapsed*engine.config.simulation_speed
            # Bounded work per turn yields to incident ingestion and SSE; never skip physics.
            steps=0
            while pending>=.2 and steps<50 and time.perf_counter()-start<.035:
                engine.step(.2); pending-=.2; steps+=1
        else:
            pending=0
        worker_metrics['lag_s']=round(pending,3)
        worker_metrics['tick_ms']=round((time.perf_counter()-start)*1000,3)
        worker_metrics['max_tick_ms']=max(worker_metrics['max_tick_ms'],worker_metrics['tick_ms'])
        if now-last_publish>=.5:
            bus.publish(engine.snapshot())
            last_publish=now


@asynccontextmanager
async def lifespan(app):
    engine.log('service','Authoritative service started; simulation state starts a new run, history is retained')
    task=asyncio.create_task(clock_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    engine.history.db.commit()


app=FastAPI(title='Railflow · railway dispatch demonstration',version='1.0.0',description='Advisory simulation only. Not a replacement for certified railway safety systems. SI units; times are seconds since the run began. Cookie session or HTTP Basic authentication.',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.middleware('http')
async def protect_and_deduplicate(request,call_next):
    if request.method in {'POST','PATCH','PUT','DELETE'}:
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return Response('Cross-origin mutation rejected',status_code=403)
        try:
            content_length=int(request.headers.get('content-length','0'))
        except ValueError:
            return Response('Invalid content length',status_code=400)
        if content_length>128_000:
            return Response('Command exceeds 128 KB',status_code=413)
        key=request.headers.get('idempotency-key')
        if key:
            if len(key)>100:
                return Response('Invalid idempotency key',status_code=400)
            body=await request.body()
            digest=hashlib.sha256(body).hexdigest()
            principal=request.cookies.get('rail_session') or request.headers.get('authorization','')
            cache_key=(principal,request.method,request.url.path,key)
            async with idempotency_lock:
                prior=idempotency.get(cache_key)
                if prior:
                    if prior['digest']!=digest:
                        return Response('Idempotency key reused for a different command',status_code=409)
                    return Response(prior['body'],status_code=prior['status'],media_type='application/json')
                response=await call_next(request)
                chunks=b''.join([chunk async for chunk in response.body_iterator])
                if response.status_code<400:
                    if len(idempotency)>=1000:
                        idempotency.pop(next(iter(idempotency)))
                    idempotency[cache_key]={'digest':digest,'body':chunks,'status':response.status_code}
                return Response(chunks,status_code=response.status_code,headers=dict(response.headers))
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    # External map tiles require a Referer; send only our origin cross-site.
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    return response


@app.exception_handler(ValueError)
async def invalid(request,exc):
    return Response(json.dumps({'detail':str(exc)}),status_code=422,media_type='application/json')


class Login(Strict):
    username:str=Field(min_length=1,max_length=32)
    password:str=Field(min_length=1,max_length=200)


@app.post('/api/login')
async def login(body:Login,response:Response,request:Request):
    host=request.client.host if request.client else 'local'
    times=[t for t in login_attempts.get(host,[]) if t>time.time()-60]
    if len(times)>=10:
        raise HTTPException(429,'Too many login attempts; retry in one minute')
    expected=credentials().get(body.username)
    if not expected or not hmac.compare_digest(expected,body.password):
        login_attempts[host]=times+[time.time()]
        raise HTTPException(401,'Invalid credentials')
    token=secrets.token_urlsafe(32)
    sessions[token]={'user':body.username,'expires':time.time()+12*3600}
    response.set_cookie('rail_session',token,httponly=True,samesite='strict',secure=os.getenv('RAIL_SECURE_COOKIE')=='1',max_age=43200)
    engine.log('auth','Signed in',actor=body.username)
    return {'user':body.username,'role':body.username}


@app.post('/api/logout')
async def logout(request:Request,response:Response,user=Depends(actor)):
    sessions.pop(request.cookies.get('rail_session',''),None)
    response.delete_cookie('rail_session')
    engine.log('auth','Signed out',actor=user)
    return {'ok':True}


@app.get('/api/me')
async def me(user=Depends(actor)):
    return {'user':user,'role':user}


@app.get('/health')
async def health():
    return {'status':'ok','service':'railflow-demo','demo_only':True,'run_id':engine.history.run}


@app.get('/api/state')
async def state(user=Depends(actor)):
    return engine.snapshot()


@app.get('/api/stream')
async def stream(request:Request,user=Depends(actor)):
    async def events():
        q=bus.subscribe(); worker_metrics['connections']+=1
        engine.log('connection','Realtime connected; full state resynchronization',actor=user,after={'last_event_id':request.headers.get('last-event-id')})
        try:
            first=engine.snapshot()
            yield f'id: {first["run_id"]}:{first["version"]}\ndata: {json.dumps(first,separators=(",",":"),ensure_ascii=False)}\n\n'
            while not await request.is_disconnected():
                try:
                    data=await asyncio.wait_for(q.get(),timeout=10)
                    yield f'id: {data["run_id"]}:{data["version"]}\ndata: {json.dumps(data,separators=(",",":"),ensure_ascii=False)}\n\n'
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            bus.subscribers.discard(q)
            engine.log('connection','Realtime disconnected; client should reconnect and resynchronize',actor=user)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})


def publish():
    s=engine.snapshot(); bus.publish(s)
    return s


@app.post('/api/control')
async def control(body:Control,user=Depends(actor)):
    before={'running':engine.running,'speed':engine.config.simulation_speed}
    if body.action in {'start','resume'}:
        engine.running=True; engine.replan(body.action)
    elif body.action=='pause':
        engine.running=False
    elif body.action=='reset':
        engine.reset(user)
    elif body.action=='speed':
        if body.speed is None:
            raise ValueError('Speed is required')
        engine.config.simulation_speed=body.speed
        engine.history.save_settings(engine.config.model_dump())
    engine.log('action',body.action,actor=user,before=before,after={'running':engine.running,'speed':engine.config.simulation_speed})
    return publish()


@app.post('/api/demo')
async def demo(body:DemoCommand,user=Depends(actor)):
    engine.load_demo(body.scenario,user)
    return publish()


@app.post('/api/trains',status_code=201)
async def create_train(body:TrainSpec,user=Depends(actor)):
    engine.add_train(body,user)
    return publish()


@app.patch('/api/trains/{tid}')
async def update_train(tid:str,body:TrainUpdate,user=Depends(actor)):
    t=engine.trains.get(tid)
    if not t:
        raise HTTPException(404,'Train not found')
    before=t.spec.model_dump()
    values=body.model_dump(exclude_unset=True)
    if values.get('importance','absent') is None:
        raise ValueError('Importance cannot be null')
    t.spec=TrainSpec(**{**before,**values})
    if 'scheduled_arrival_s' in values:
        t.stop_points[-1]['scheduled']=values['scheduled_arrival_s']
    engine.log('importance' if 'importance' in values else 'timetable','Train configuration updated',[tid],user,before,t.spec.model_dump())
    engine.replan('train preferences or timetable updated; committed routes retained')
    return publish()


@app.delete('/api/trains/{tid}')
async def remove_train(tid:str,user=Depends(actor)):
    t=engine.trains.get(tid)
    if not t:
        raise HTTPException(404,'Train not found')
    if t.launched and t.state not in {'arrived','completed'}:
        raise HTTPException(409,'Only staged or terminal trains can be withdrawn; committed movements remain locked')
    engine.log('removal','Train withdrawn',[tid],user,before=t.spec.model_dump())
    del engine.trains[tid]; engine.replan('train removed')
    return publish()


@app.post('/api/incidents',status_code=201)
async def incident(body:IncidentSpec,user=Depends(actor)):
    engine.add_incidents([body],user)
    return publish()


@app.post('/api/incidents/batch',status_code=201)
async def incident_batch(body:list[IncidentSpec],user=Depends(actor)):
    if not 1<=len(body)<=10:
        raise ValueError('Batch size must be 1–10 incidents')
    engine.add_incidents(body,user)
    return publish()


@app.post('/api/incidents/{iid}/clear')
async def clear_incident(iid:str,user=Depends(actor)):
    engine.clear_incident(iid,user)
    return publish()


@app.post('/api/ingest')
async def ingest(body:IngestEvent,user=Depends(actor)):
    last=engine.ingest_sequences.get(body.source,-1)
    if body.sequence<=last or body.sim_time<engine.sim_time-30 or body.sim_time>engine.sim_time+5:
        engine.metrics['rejected_ingest']+=1
        engine.log('ingest','Duplicate, out-of-order or stale external event rejected',actor=body.source,after=body.model_dump())
        raise HTTPException(409,'Event is stale, out of order, duplicated or in the future')
    engine.add_incidents([body.incident],body.source)
    engine.ingest_sequences[body.source]=body.sequence
    return publish()


@app.get('/api/config')
async def get_config(user=Depends(actor)):
    return engine.config.model_dump()


@app.put('/api/config')
async def put_config(body:Settings,user=Depends(admin)):
    if body.clearance_m!=engine.config.clearance_m and any(t.launched and t.state!='completed' for t in engine.trains.values()):
        raise HTTPException(409,'Clearance cannot change while trains hold track')
    if body.signal_block_m!=engine.config.signal_block_m and any(t.launched and t.state!='completed' for t in engine.trains.values()):
        raise HTTPException(409,'Signal block layout cannot change while trains hold track; reset the simulation first')
    before=engine.config.model_dump()
    # Revalidate configured sidings before enlarging their clearance margin.
    for t in engine.trains.values():
        if t.spec.via_siding is not None and engine.network.edges[t.spec.via_siding]['length_m']<t.spec.length_m+2*body.clearance_m:
            raise HTTPException(409,'New clearance would make an existing train too long for its siding')
    if body.random_seed!=engine.config.random_seed:
        engine.rng.seed(body.random_seed)
    engine.config=body; engine.history.save_settings(body.model_dump())
    engine.log('configuration','Settings updated',actor=user,before=before,after=body.model_dump())
    engine.replan('configuration changed'); publish()
    return body


@app.get('/api/network')
async def network(user=Depends(actor)):
    n=engine.network
    return {'stats':n.stats,'demo':engine.demo,'stations':[{'vertex':s['vertex'],'name':s['name'] or n.label(s['vertex']),'name_en':s['name_en'],'snap_dist_m':s['snap_dist_m']} for s in n.stations if s['vertex'] is not None and s['type']!='tram_stop' and s['name']], 'hashes':n.hashes,'excluded_synthetic_edges':sum(1 for e in n.edges if n.ways[e['way']].get('synthetic')),'assumptions':['Synthetic gap-healing links excluded; suspected gaps never routable.','Source shared vertices used; no new connectivity inferred at visual intersections.','Assumed automatic-block overlay splits long edges; actual KTZ signal positions and block lengths are unavailable. See Configuration for the simulated block size.','Reservations advance with each train and protect its braking distance; occupied blocks and reserved blocks are shown separately.','Opposing and crossing routes retain conservative direction protection to the next scheduled stop. Actual station-to-station locking requires verified reception tracks and interlocking plans.','Station capacity: one exclusive vertex, no invented platforms.','Switch compatibility inferred from existing edge headings (≤90°); not a verified interlocking table.','Train origins stage outside occupancy; terminal trains are withdrawn after service.','Usable siding length: existing edge length minus two configured clearances.','Dispatch preference weights and FIFO fairness are simulator heuristics, not a verified KTZ priority order.']}


@app.get('/api/history')
async def history(search:str='',kind:str='',limit:int=Query(300,ge=1,le=5000),run:str|None=None,user=Depends(actor)):
    return engine.history.query(search,kind,limit,run)


@app.get('/api/replay/runs')
async def replay_runs(user=Depends(actor)):
    return engine.history.runs()


@app.get('/api/replay')
async def replay(start_s:float=Query(0,ge=0),end_s:float|None=Query(None,ge=0),run:str|None=None,user=Depends(actor)):
    end=engine.sim_time if end_s is None else end_s
    if end<start_s or end-start_s>900:
        raise ValueError('Replay range must be between 0 and 900 simulation seconds')
    return {'read_only':True,'frames':engine.history.replay(start_s,end,run)}


@app.get('/api/report.csv')
async def report(user=Depends(actor)):
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(['Railflow demonstration and advisory system; not certified railway safety equipment'])
    writer.writerow(['event_id','run','simulation_seconds','kind','actor','entities','message','before','after'])
    def safe(v):
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    for e in reversed(engine.history.query(limit=100000,run=engine.history.run)):
        writer.writerow([safe(v) for v in [e['id'],e['run'],e['sim_time'],e['kind'],e['actor'],';'.join(e['entities']),e['message'],json.dumps(e['before'],ensure_ascii=False),json.dumps(e['after'],ensure_ascii=False)]])
    for t in engine.trains.values():
        writer.writerow(['',engine.history.run,engine.sim_time,'train_summary',user,t.spec.id,'Delay (s), energy (kWh), actual arrival','',json.dumps({'delay_s':engine.delay(t),'energy_kwh':t.energy,'arrivals':t.arrivals})])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="railflow-report.csv"'})


@app.get('/api/metrics')
async def metrics(user=Depends(actor)):
    return {**engine.metrics,**worker_metrics,'subscribers':len(bus.subscribers),'dropped_snapshots':bus.dropped,'history_retention_hours':engine.config.retention_hours}


@app.get('/',include_in_schema=False)
async def index():
    return FileResponse(ROOT/'static/index.html')


@app.get('/map.html',include_in_schema=False)
async def original_map():
    return FileResponse(ROOT/'map.html')


@app.get('/map_data.js',include_in_schema=False)
async def map_data():
    return FileResponse(ROOT/'map_data.js',media_type='text/javascript')


@app.get('/map-live',include_in_schema=False)
async def live_map():
    # Original bytes on disk remain untouched. Runtime additions are overlays/menus only.
    html=(ROOT/'map.html').read_text()
    html=html.replace('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css','/static/vendor/leaflet.css').replace('https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js','/static/vendor/leaflet.js')
    return HTMLResponse(html.replace('</body>','<script src="/static/map-overlay.js"></script></body>'))


@app.get('/presentation',include_in_schema=False)
async def presentation():
    return FileResponse(ROOT/'artifacts/presentation.html')
