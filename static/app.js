'use strict';
const $=id=>document.getElementById(id);
const tr=(source,params)=>window.RailI18n.t(source,params);
const message=source=>window.RailI18n.message(source);
// Store source messages so a language switch can update visible notifications.
const transientMessages=new Map();
function translatedText(id,source,params){transientMessages.set(id,{source,params});$(id).textContent=params?tr(source,params):message(source);}
function label(source){return `<span data-i18n="${esc(source)}">${esc(tr(source))}</span>`;}
let connectionSource='Connecting…',connectionParams;
function connectionText(source,params){connectionSource=source;connectionParams=params;$('connection').textContent=tr(source,params);}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=s=>s==null?'—':[Math.floor(s/3600),Math.floor(s/60)%60,Math.floor(s)%60].map(v=>String(v).padStart(2,'0')).join(':');
const num=(v,n=1)=>v==null?'—':Number(v).toFixed(n);
let liveState=null,displayState=null,selected=null,network=null,config=null,user=null,eventSource=null;
let lastReceived=0,lastVersion=-1,lastRun=null,retry=500,reconnectTimer=null,mapReady=false,framePending=false,queued=null;
let replaying=false,replayFrames=[],replayTimer=null,currentTab='overview',lastActionsId=null,toastTimer=null;
window.railMetrics={renderMs:[],mapLatencyMs:[],events:0,discarded:0,reconnects:0};
const receiptTimes=new Map();
async function api(path,method='GET',body){
  if(replaying&&method!=='GET')throw new Error('Replay is read-only. Return to live mode to make changes.');
  const options={method,headers:{}};
  if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  if(method!=='GET')options.headers['Idempotency-Key']=crypto.randomUUID();
  const response=await fetch(path,options);
  if(!response.ok){let message;try{const e=await response.json();message=typeof e.detail==='string'?e.detail:JSON.stringify(e.detail);}catch{message=response.statusText;}
    if(response.status===401&&path!=='/api/login'){disconnect();$('application').hidden=true;$('login-screen').hidden=false;}
    throw new Error(message||'Request failed');}
  return response.headers.get('content-type')?.includes('json')?response.json():response.text();
}
function notify(source,error=false,params){translatedText('alert',source,params);$('alert').hidden=false;$('alert').style.background=error?'#fce9e5':'#fcf1d9';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('alert').hidden=true,9000);}
async function action(path,body,method='POST'){try{const result=await api(path,method,body);if(result?.trains)receive(result);return result;}catch(e){notify(e.message,true);return null;}}
function disconnect(){eventSource?.close();eventSource=null;clearTimeout(reconnectTimer);connectionText('Disconnected');$('connection').className='connection offline';}
function connect(){
  eventSource?.close();eventSource=new EventSource('/api/stream');
  eventSource.onopen=()=>{retry=500;connectionText('Connected · 2 Hz');$('connection').className='connection';};
  eventSource.onmessage=event=>{try{receive(JSON.parse(event.data));}catch(error){notify('Invalid stream message: {error}',true,{error:message(error.message)});}};
  eventSource.onerror=()=>{disconnect();connectionText('Disconnected · reconnecting');reconnectTimer=setTimeout(()=>{window.railMetrics.reconnects++;connect();},retry);retry=Math.min(15000,retry*2);};
}
function receive(s){
  if(!s||!Number.isFinite(s.version)||!Array.isArray(s.trains))return;
  lastReceived=performance.now();
  if(s.run_id===lastRun&&s.version<=lastVersion){window.railMetrics.discarded++;return;}
  lastRun=s.run_id;lastVersion=s.version;liveState=s;window.railMetrics.events++;
  if(replaying)return;
  // Some browsers defer animation frames and timers until slow iframe resources finish.
  if(document.readyState!=='complete'){render(s,lastReceived);return;}
  queued={state:s,received:lastReceived};
  if(!framePending){
    framePending=true;
    const flush=()=>{if(!framePending)return;framePending=false;const item=queued;queued=null;if(item)render(item.state,item.received);};
    requestAnimationFrame(flush);
    // A loading/background document can throttle animation frames. Keep state current.
    setTimeout(flush,100);
  }
}
setInterval(()=>{
  if(!user)return;
  const age=(performance.now()-lastReceived)/1000;
  if(age>3){connectionText(age>10?'Disconnected · stale data':'Stale data · {seconds}s',{seconds:Math.floor(age)});$('connection').className='connection '+(age>10?'offline':'stale');}
},1000);
async function enter(){
  user=await api('/api/me');$('user-name').textContent=user.user;$('login-screen').hidden=true;$('application').hidden=false;
  // Initialize Leaflet only after its iframe has a visible, measurable viewport.
  if($('rail-map').getAttribute('src')==='about:blank'){$('rail-map').getBoundingClientRect();$('rail-map').src='/map-live';}
  [network,config]=await Promise.all([api('/api/network'),api('/api/config')]);
  renderStationOptions();
  renderSettings();updateAssets();connect();receive(await api('/api/state'));
}
$('login-form').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target);await api('/api/login','POST',Object.fromEntries(f));await enter();}catch(error){translatedText('login-error',error.message);}};
$('logout').onclick=async()=>{await action('/api/logout',{});user=null;disconnect();$('application').hidden=true;$('login-screen').hidden=false;};
api('/api/me').then(()=>enter()).catch(()=>{});
function postMap(s){if(mapReady){receiptTimes.set(s.version,performance.now());if(receiptTimes.size>20)receiptTimes.delete(receiptTimes.keys().next().value);$('rail-map').contentWindow.postMessage({type:'state',state:s,selected,replay:replaying},location.origin);}}
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('rail-map').contentWindow)return;
  if(event.data.type==='map-ready'){mapReady=true;$('map-loading').hidden=true;if(displayState)postMap(displayState);}
  if(event.data.type==='select-train'){selected=event.data.id;if(displayState)render(displayState,performance.now());}
  if(event.data.type==='map-rendered'){const started=receiptTimes.get(event.data.version);if(started){window.railMetrics.mapLatencyMs.push(performance.now()-started);receiptTimes.delete(event.data.version);window.railMetrics.mapLatencyMs=window.railMetrics.mapLatencyMs.slice(-300);}}
});
$('fit-trains').onclick=()=>$('rail-map').contentWindow.postMessage({type:'focus'},location.origin);
$('map-controls').onclick=()=>$('rail-map').contentWindow.postMessage({type:'layers'},location.origin);
const titles={overview:['Live overview','Every movement, in view.','A live picture of the network. A reason behind every decision.'],timetable:['Timetable & diagram','The plan. And the progress.','Compare arrival targets with actual movement across the network.'],incidents:['Incidents','A clear route to recovery.','Create disruptions, see their effects, and inspect feasible alternatives.'],history:['History & replay','Every decision has a history.','Replay the operation and export a traceable dispatch report.'],settings:['Configuration','Tune the operation.','Adjust preferences and demo parameters without changing the railway.']};
function tab(name){currentTab=name;document.querySelectorAll('.tab').forEach(el=>el.classList.toggle('active',el.id==='tab-'+name));document.querySelectorAll('.nav-button').forEach(el=>el.classList.toggle('active',el.dataset.tab===name));[$('page-name').textContent,$('page-title').textContent,$('page-subtitle').textContent]=titles[name].map(source=>tr(source));if(displayState)render(displayState,performance.now());if(name==='history')loadLogs();}
document.querySelectorAll('[data-tab]').forEach(el=>el.onclick=()=>tab(el.dataset.tab));
$('start').onclick=()=>action('/api/control',{action:liveState?.sim_time?'resume':'start'});
$('pause').onclick=()=>action('/api/control',{action:'pause'});
$('reset').onclick=()=>action('/api/control',{action:'reset'});
$('speed').onchange=e=>action('/api/control',{action:'speed',speed:+e.target.value});
$('load-demo').onclick=async()=>{const r=await action('/api/demo',{scenario:$('scenario').value});if(r){selected=null;notify('Scenario loaded. Press Start to advance the simulation.');}};
function render(s,received){
  const begin=performance.now();displayState=s;
  if(!s.trains.some(t=>t.id===selected))selected=s.trains[0]?.id||null;
  $('sim-clock').textContent=clock(s.sim_time);$('speed').value=String(s.simulation_speed);$('start').textContent=tr(s.running?'▶ Running':s.sim_time?'▶ Resume':'▶ Start');$('start').disabled=s.running||replaying;$('pause').disabled=!s.running||replaying;
  const active=s.trains.filter(t=>!['completed','scheduled'].includes(t.state));
  const incidents=s.incidents.filter(i=>i.status==='active');
  $('metric-trains').innerHTML=`${active.length} <small>${tr('active')}</small>`;$('metric-trains-sub').textContent=tr('{services} services · {waiting} waiting',{services:s.trains.length,waiting:s.trains.filter(t=>t.state==='waiting'||t.state==='incident_hold').length});
  $('metric-quality').innerHTML=`${s.quality.score??'—'} <small>/ 100</small>`;$('metric-quality-sub').textContent=message(s.quality.status);
  const scheduled=s.trains.filter(t=>t.spec.scheduled_arrival_s!=null);
  $('metric-delay').innerHTML=`${num(scheduled.reduce((a,t)=>a+t.delay_s,0)/Math.max(1,scheduled.length)/60)} <small>${tr('min')}</small>`;
  $('metric-incidents').innerHTML=`${incidents.length} <small>${tr('open')}</small>`;$('incident-count').textContent=incidents.length;
  $('metric-incident-sub').textContent=incidents.length?tr('{count} affected services',{count:new Set(incidents.flatMap(i=>i.affected_trains)).size}):tr('No active disruptions');
  $('map-plan').textContent=tr('Plan v{version} · {mode}',{version:s.plan_version,mode:tr(replaying?'REPLAY':'LIVE')});
  $('map-region').textContent=tr(s.dispatcher?'Kazakhstan · simulated automatic blocks':'Kazakhstan · original track geometry');
  $('quality-number').textContent=s.quality.score??'—';$('quality-gauge').style.setProperty('--score',s.quality.score??0);$('quality-gauge').style.setProperty('--gauge',s.quality.status==='Critical'?'#c74848':s.quality.status==='Attention'?'#c7a050':'#729760');$('quality-status').textContent=message(s.quality.status);
  const factorNames={schedule:'Schedule adherence',capacity:'Capacity use',energy:'Energy efficiency',conflicts:'Conflict freedom',stopping:'Stopping accuracy'};
  $('quality-factors').innerHTML=Object.entries(s.quality.factors).map(([key,f])=>`<div class="factor"><span>${esc(tr(factorNames[key]||key))}</span><b>${num(f.contribution)}/${f.weight*100}</b><div class="bar"><div style="width:${f.value}%"></div></div></div>`).join('');
  $('quality-window').textContent=tr('{minutes}-minute rolling window · {seconds}s observed. Constraints always take precedence.',{minutes:Math.round(s.quality.window_s/60),seconds:s.quality.observed_s??0});
  $('quality-reasons').textContent=s.quality.reasons.slice(0,2).map(message).join(' · ');
  renderTrains(s);renderSelected(s);renderTimetable(s);renderIncidents(s);renderDecisions(s);
  postMap(s);
  if(currentTab==='timetable')drawDistance(s);
  if(s.safety_errors.length)notify('Simulation constraint alert: {errors}',true,{errors:s.safety_errors.map(message).join('; ')});
  if(s.deadlock.length)notify('Deadlock detected; trains held safely: {trains}',true,{trains:s.deadlock.join(', ')});
  $('replan-latency').textContent=tr('{value} ms',{value:num(s.metrics.replan_ms,1)});
  const duration=performance.now()-received;
  window.railMetrics.renderMs.push(duration);window.railMetrics.renderMs=window.railMetrics.renderMs.slice(-300);
  $('latency').textContent=tr('State v{version} · UI {ui} ms · plan {plan} ms',{version:s.version,ui:num(duration,0),plan:num(s.metrics.replan_ms,1)});
  window.railMetrics.lastRenderWorkMs=performance.now()-begin;
}
function renderTrains(s){
  const q=$('train-search').value.toLowerCase(),filter=$('train-filter').value;
  const rows=s.trains.filter(t=>(t.id+' '+t.name+' '+t.destination).toLowerCase().includes(q)).filter(t=>filter==='all'||(filter==='running'?t.speed_kmh>0:filter==='waiting'?['waiting','incident_hold','dwelling'].includes(t.state):t.state===filter));
  $('train-count').textContent=s.trains.length;
  $('train-rows').innerHTML=rows.map(t=>`<tr class="train-row ${t.id===selected?'selected':''}" data-train="${esc(t.id)}" tabindex="0"><td><div class="train-id"><span class="train-icon">▣</span><div><strong>${esc(t.id)}</strong><small>${esc(t.name)}</small></div></div></td><td><strong>${esc(message(t.origin))}</strong><small>→ ${esc(message(t.destination))}</small></td><td><span class="status-pill ${t.state}">${esc(tr(t.state.replaceAll('_',' ')))}</span></td><td><span class="importance ${t.importance>=8?'high':''}">${t.importance} / 10</span></td><td>${num(t.speed_kmh)} <small>${tr('km/h')}</small></td><td>${t.delay_s>0?'+'+num(t.delay_s/60):tr('On time')}${t.delay_s>0?'<small>'+tr('min predicted')+'</small>':''}</td><td class="reason-cell">${esc(message(t.reason))}</td></tr>`).join('')||`<tr><td colspan="7" class="empty">${tr('No services yet. Load a demonstration or add your first train.')}</td></tr>`;
}
$('train-rows').onclick=e=>{const row=e.target.closest('[data-train]');if(row){selected=row.dataset.train;render(displayState,performance.now());}};
$('train-rows').onkeydown=e=>{if(e.key==='Enter')e.target.click();};
$('train-search').oninput=() => displayState&&renderTrains(displayState);$('train-filter').onchange=()=>displayState&&renderTrains(displayState);
function renderSelected(s){
  const t=s.trains.find(t=>t.id===selected);
  if(!t){$('ato-stats').innerHTML=label('No train selected');$('authority-details').textContent='';$('selected-actions').innerHTML='';$('selected-title').textContent=tr('Driving advisory');$('selected-subtitle').textContent=tr('Select a train for recommendations');lastActionsId=null;return;}
  $('selected-title').textContent=tr('{id} · driving advisory',{id:t.id});$('selected-subtitle').textContent=`${t.name} · ${message(t.reason)}`;
  const ahead=t.authority_m==null?0:Math.max(0,t.authority_m-t.distance_m);
  const occupied=t.occupied_blocks?.length??t.occupied_edges.length;
  const reserved=t.reserved_blocks?.filter(b=>!b.occupied).length??t.reserved_edges.filter(e=>!t.occupied_edges.includes(e)).length;
  $('authority-details').textContent=tr('Authority ahead: {distance} km · {occupied} occupied blocks · {reserved} reserved ahead',{distance:num(ahead/1000,2),occupied,reserved});
  $('ato-stats').innerHTML=`<div><strong>${num(t.ato.recommended_kmh)} <small>${tr('km/h')}</small></strong><span>${tr('RECOMMENDED')}</span></div><div><strong>${num(t.speed_kmh)} <small>${tr('km/h')}</small></strong><span>${tr('ACTUAL')}</span></div><div><strong>${clock(t.predicted_arrival_s)}</strong><span>${tr('PREDICTED ARRIVAL')}</span></div><div><strong>${num(t.energy_kwh)} <small>${tr('kWh')}</small></strong><span>${tr('SIMPLIFIED ENERGY')}</span></div>`;
  if(lastActionsId!==t.id){
    lastActionsId=t.id;
    $('selected-actions').innerHTML=`<label>${label('Importance')} <input id="selected-importance" type="number" min="1" max="10" value="${t.importance}"></label><label>${label('Arrival (s)')} <input id="selected-arrival" type="number" min="0" value="${t.spec.scheduled_arrival_s??''}"></label><button id="update-train" class="live-action" data-i18n="Apply &amp; replan">${tr("Apply & replan")}</button><button id="remove-train" class="live-action" data-i18n="Withdraw">${tr("Withdraw")}</button>`;
    $('update-train').onclick=()=>{const i=$('selected-importance'),a=$('selected-arrival');if(!i.reportValidity()||!a.reportValidity())return;action('/api/trains/'+t.id,{importance:+i.value,scheduled_arrival_s:a.value===''?null:+a.value},'PATCH');};
    $('remove-train').onclick=()=>action('/api/trains/'+t.id,undefined,'DELETE');
  }
  $('selected-actions').querySelectorAll('button,input').forEach(el=>el.disabled=replaying);
  if(currentTab==='overview')drawChart($('ato-chart'),[{name:tr('Recommended'),color:'#3f8356',points:t.ato.points.map(p=>[p.distance_m/1000,p.recommended_kmh])},{name:tr('Actual now'),color:'#d1a25e',points:[[0,t.speed_kmh],[Math.max(.01,t.ato.points.at(-1)?.distance_m/1000||.1),t.speed_kmh]],dash:true}], tr('Distance ahead · km'),tr('km/h'));
}
function renderTimetable(s){
  $('timetable-rows').innerHTML=s.trains.flatMap(t=>[{label:tr('Departure · {origin}',{origin:message(t.origin)}),scheduled:t.spec.departure_s,actual:t.actual_departure_s,dwell:0},...t.stops.map((p,i)=>({label:message(p.label),scheduled:p.scheduled,actual:t.arrivals[i]?.time,dwell:p.dwell}))].map((p,i)=>`<tr><td><strong>${esc(t.id)}</strong></td><td>${esc(p.label)}</td><td>${clock(p.scheduled)}</td><td>${clock(p.actual)}</td><td>${esc(tr('{value}s',{value:p.dwell}))}</td><td>${i===t.stops.length?clock(t.predicted_arrival_s)+' / +'+num(t.delay_s/60)+' '+tr('min'):'—'}</td></tr>`)).join('')||`<tr><td colspan="6" class="empty">${tr('No timetable entries.')}</td></tr>`;
}
function drawChart(canvas,series,xLabel,yLabel){
  const rect=canvas.getBoundingClientRect();if(rect.width<20)return;
  const w=rect.width-28,h=+(canvas.dataset.logicalHeight||canvas.getAttribute('height')),dpr=devicePixelRatio||1;
  canvas.style.height=h+'px';canvas.width=w*dpr;canvas.height=h*dpr;
  // Explicit CSS height prevents successive device-pixel expansion.
  canvas.setAttribute('height',h);canvas.width=w*dpr;canvas.height=h*dpr;
  canvas.dataset.logicalHeight=h;
  const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
  const left=40,right=15,top=22,bottom=30;
  const points=series.flatMap(s=>s.points).filter(p=>p.every(Number.isFinite));
  const maxX=Math.max(.1,...points.map(p=>p[0])),maxY=Math.max(10,...points.map(p=>p[1]));
  const x=v=>left+v/maxX*(w-left-right),y=v=>h-bottom-v/maxY*(h-top-bottom);
  ctx.font='9px Arial';ctx.strokeStyle='#e8eee2';ctx.fillStyle='#8a9781';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){const yy=y(maxY*i/4);ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillText(num(maxY*i/4,0),6,yy+3);ctx.fillText(num(maxX*i/4,1),x(maxX*i/4)-6,h-bottom+14);}
  ctx.fillText(yLabel,5,12);ctx.fillText(xLabel,w/2-35,h-2);
  series.forEach((s,index)=>{ctx.strokeStyle=s.color;ctx.lineWidth=2;ctx.setLineDash(s.dash?[5,4]:[]);ctx.beginPath();s.points.forEach((p,i)=>{if(i)ctx.lineTo(x(p[0]),y(p[1]));else ctx.moveTo(x(p[0]),y(p[1]));});ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=s.color;ctx.fillText(s.name,left+index*125,12);});
}
function chartFixed(canvas,series,x,y){const h=+(canvas.dataset.logicalHeight||canvas.getAttribute('height'));canvas.setAttribute('height',h);drawChart(canvas,series,x,y);}
const palette=['#468258','#c69b58','#5d89bc','#9b78ae','#ba6d6d'];
function drawDistance(s){const lines=[];s.trains.forEach((t,i)=>{lines.push({name:t.id,color:palette[i%palette.length],points:t.trace.map(p=>[p[0]/60,p[1]/1000])});if(t.spec.scheduled_arrival_s!=null)lines.push({name:'',color:palette[i%palette.length],dash:true,points:[[t.spec.departure_s/60,0],[t.spec.scheduled_arrival_s/60,t.total_m/1000]]});});chartFixed($('distance-chart'),lines,tr('Simulation time · min'),tr('km'));}
function renderDecisions(s){
  const events=s.events.filter(e=>['plan','conflict','departure','reroute','arrival'].includes(e.kind)).slice(-5).reverse();
  $('decision-feed').innerHTML=events.map(e=>`<div class="decision"><span class="decision-dot"></span><div><b>${esc(e.entities.join(' · ')||tr('Central dispatcher'))}</b>${esc(message(e.message))}<small>${clock(e.sim_time)} · ${esc(tr(e.kind))}</small></div></div>`).join('')||`<div class="empty">${tr('Dispatch explanations appear here when a service is planned.')}</div>`;
}
function renderIncidents(s){
  const list=s.incidents.filter(i=>i.status!=='cleared');
  $('active-incidents').innerHTML=list.map(i=>`<div class="incident-item"><div><strong>${esc(tr(i.kind.replaceAll('_',' ')))}</strong> <span class="status-pill ${i.status==='active'?'incident_hold':''}">${esc(tr(i.status))}</span><small>${esc(i.id)} · ${esc(tr(i.asset_type))} ${esc(i.asset_id)} · ${esc(tr('starts {time}',{time:clock(i.start_s)}))}</small><small>${i.end_s==null?tr('Manual clearance'):tr('Clears {time}',{time:clock(i.end_s)})} · ${esc(tr('affected {trains}',{trains:i.affected_trains.join(', ')||tr('none')}))}</small></div><button class="clear-incident live-action" data-id="${esc(i.id)}" ${replaying?'disabled':''}>${tr('Clear')}</button></div>`).join('')||`<div class="empty">${tr('No active incidents. The network is ready.')}</div>`;
  $('alternatives').innerHTML=s.alternatives.map(a=>`<div class="alternative"><h3>${esc(a.train_id)} · ${esc(a.incident_id)}</h3><p>${esc(message(a.reason))}</p>${a.actions.map(p=>`<div class="alternative-action"><strong>${esc(message(p.action))}${p.recommended?' · '+tr('recommended'):''}</strong><small>${esc(tr('Arrival {arrival} · delay {delay} · recovery {recovery}',{arrival:clock(p.arrival_s),delay:p.delay_s==null?tr('unknown'):num(p.delay_s/60)+' '+tr('min'),recovery:p.recovery_s==null?tr('manual clearance'):tr('{value}s',{value:num(p.recovery_s,0)})}))}</small><small>${esc(tr('Predicted MQI schedule contribution Δ {value}',{value:p.quality_schedule_delta==null?tr('unknown'):tr('{value} pt',{value:num(p.quality_schedule_delta)})}))} · ${esc(message(p.note))}</small></div>`).join('')}</div>`).join('')||`<div class="empty">${tr('Recovery plans appear when an incident affects a service.')}</div>`;
}
$('active-incidents').onclick=e=>{const b=e.target.closest('.clear-incident');if(b)action('/api/incidents/'+b.dataset.id+'/clear',{});};
const assetTypes={train_delay:['train','station'],train_breakdown:['train'],signal_failure:['signal','switch'],track_closure:['edge','station','switch'],speed_restriction:['edge']};
function updateIncidentType(){const kinds=assetTypes[$('incident-kind').value];$('incident-asset-type').innerHTML=kinds.map(k=>`<option value="${k}">${esc(tr(k))}</option>`).join('');updateAssets();}
function updateAssets(){const kind=$('incident-asset-type').value;let values=[];if(kind==='train')values=(liveState?.trains||[]).map(t=>[t.id,t.name]);else if(kind==='signal')values=(liveState?.signals||[]).map(s=>[s.id,tr(s.aspect)]);else if(kind==='edge')values=[...new Set((liveState?.trains||[]).flatMap(t=>t.route_edges))].map(id=>[id,tr('Existing track section')]);else if(kind==='switch')values=(liveState?.switches||[]).map(s=>[s.vertex,tr('Locked by {owner}',{owner:s.owner})]);else if(kind==='station')values=(network?.stations||[]).map(s=>[s.vertex,s.name]);$('asset-options').innerHTML=values.map(([v,n])=>`<option value="${esc(v)}">${esc(n)}</option>`).join('');}
$('incident-kind').onchange=updateIncidentType;$('incident-asset-type').onchange=updateAssets;$('incident-asset').onfocus=updateAssets;updateIncidentType();
$('incident-form').onsubmit=async e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));['start_s','duration_s'].forEach(k=>f[k]=f[k]===''?null:+f[k]);f.speed_kmh=+f.speed_kmh;const result=await action('/api/incidents',f);if(result)notify('Incident registered and dispatch plan reconsidered.');};
$('incident-burst').onclick=()=>{if(!liveState?.trains.length)return notify('Load a scenario first.');action('/api/incidents/batch',Array.from({length:10},(_,i)=>({kind:i%2?'train_delay':'train_breakdown',asset_type:'train',asset_id:liveState.trains[i%liveState.trains.length].id,duration_s:30+i*5,note:'Repeatable 10-incident burst'})));};
$('add-train-open').onclick=()=>{const form=$('train-form');if(network){form.elements.origin.value=network.demo.origin;form.elements.destination.value=network.demo.destination;}form.elements.departure_s.value=Math.floor(liveState?.sim_time||0);translatedText('train-error','');$('train-dialog').showModal();};
$('train-form').onsubmit=async e=>{
  e.preventDefault();const form=Object.fromEntries(new FormData(e.target));
  for(const k of ['origin','destination','departure_s','importance','length_m','max_speed_kmh','acceleration_mps2','braking_mps2','mass_t','give_way_s'])form[k]=+form[k];
  form.scheduled_arrival_s=form.scheduled_arrival_s===''?null:+form.scheduled_arrival_s;form.via_siding=form.via_siding===''?null:+form.via_siding;
  form.stops=form.stops.trim()?form.stops.trim().split('\n').map(line=>{const p=line.split(',').map(s=>s.trim());return {vertex:+p[0],dwell_s:p[1]?+p[1]:30,scheduled_arrival_s:p[2]?+p[2]:null};}):[];
  try{const result=await api('/api/trains','POST',form);receive(result);$('train-dialog').close();notify('Train created. Dispatch constraints validated.');}catch(error){translatedText('train-error',error.message);}
};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
let openInfo=null,logRows=[];
const guideParagraphs=[
  'Load Opposing trains · passing loop, then press Start. At 30×, the local reaches the siding in about 30 wall-clock seconds. The express crosses on the existing main track while the local waits.',
  'Select a train to see its recommended speed, authority and energy estimate. Importance may be changed while running; committed movements retain their locks.',
  'Use Incidents to interrupt a train, fail a signal or close a section. Clear the incident to resume. History supports a read-only replay and CSV export.',
  'Simulation time starts at 00:00:00. All timetable, incident and dwell inputs use seconds on this clock. Stage origins and terminal withdrawal are simplified boundary operations.',
  'Demonstration and advisory system. Not a replacement for certified railway safety systems.'
];
function renderInfo(){
  if(!openInfo)return;
  $('info-title').textContent=tr(openInfo==='guide'?'Operating guide':'Infrastructure assumptions');
  $('info-content').innerHTML=openInfo==='guide'
    ?guideParagraphs.map((source,i)=>`<p>${i===4?'<b>':''}${esc(tr(source))}${i===4?'</b>':''}</p>`).join('')
    :`<p>${esc(tr('Map, source graph and builder are preserved byte-for-byte. {count} synthetic links are excluded from dispatching.',{count:network?.excluded_synthetic_edges??'—'}))}</p><ul>${(network?.assumptions||[]).map(source=>`<li>${esc(message(source))}</li>`).join('')}</ul><p>${esc(tr('Passing demo: existing siding E{edge}, {length} m. Unknown topology is never repaired by the simulator.',{edge:network?.demo.siding_edge??'—',length:num(network?.demo.siding_length_m)}))}</p>`;
}
function info(kind){openInfo=kind;renderInfo();$('info-dialog').showModal();}
$('help-open').onclick=()=>info('guide');
$('assumptions-open').onclick=()=>info('assumptions');
function renderLogs(){
  const expanded=new Set([...$('log-rows').querySelectorAll('details[open]')].map(el=>el.dataset.event));
  $('log-rows').innerHTML=logRows.filter(e=>!replaying||e.sim_time<=displayState.sim_time).map(e=>`<tr><td>${clock(e.sim_time)}<small>${esc(e.id.slice(0,8))}</small></td><td><strong>${esc(tr(e.kind))}</strong><small>${esc(e.actor)}</small></td><td>${esc(e.entities.join(', '))}</td><td>${esc(message(e.message))}<details data-event="${esc(e.id)}" ${expanded.has(e.id)?'open':''}><summary>${tr('Before / after')}</summary><pre>${esc(JSON.stringify({before:e.before,after:e.after},null,2))}</pre></details></td></tr>`).join('')||`<tr><td colspan="4" class="empty">${tr('No matching events.')}</td></tr>`;
}
async function loadLogs(){try{logRows=await api('/api/history?'+new URLSearchParams({search:$('log-search').value,kind:$('log-kind').value,limit:300,...(replaying&&displayState?{run:displayState.run_id}:{})}));renderLogs();}catch(error){notify(error.message,true);}}
let logTimer;$('log-search').oninput=()=>{clearTimeout(logTimer);logTimer=setTimeout(loadLogs,250);};$('log-kind').onchange=loadLogs;$('refresh-log').onclick=loadLogs;
function replayFrame(index){if(!replayFrames.length)return;replaying=true;document.body.classList.add('replaying');$('replay-banner').hidden=false;$('return-live').hidden=false;document.querySelectorAll('.live-action').forEach(e=>e.disabled=true);const s=replayFrames[index];$('replay-slider').value=index;$('replay-time').textContent=tr('{time} · read only',{time:clock(s.sim_time)});render(s,performance.now());}
$('load-replay').onclick=async()=>{try{const end=liveState?.sim_time||0;const result=await api(`/api/replay?start_s=${Math.max(0,end-+$('replay-length').value)}&end_s=${end}`);replayFrames=result.frames;if(!replayFrames.length)return notify('Start the simulation to record replay snapshots.');$('replay-slider').max=replayFrames.length-1;$('play-replay').disabled=false;replayFrame(0);loadLogs();}catch(error){notify(error.message,true);}};
$('replay-slider').oninput=e=>replayFrame(+e.target.value);
$('play-replay').onclick=()=>{if(replayTimer){clearInterval(replayTimer);replayTimer=null;$('play-replay').textContent=tr('▶ Play');return;}$('play-replay').textContent=tr('Ⅱ Pause');replayTimer=setInterval(()=>{const i=+$('replay-slider').value+1;if(i>=replayFrames.length){clearInterval(replayTimer);replayTimer=null;$('play-replay').textContent=tr('▶ Play');}else replayFrame(i);},100);};
$('return-live').onclick=()=>{replaying=false;clearInterval(replayTimer);replayTimer=null;document.body.classList.remove('replaying');$('replay-banner').hidden=true;$('return-live').hidden=true;document.querySelectorAll('.live-action').forEach(e=>e.disabled=false);if(liveState)render(liveState,performance.now());loadLogs();};
const settingLabels={normal_threshold:'Normal threshold',attention_threshold:'Attention threshold',simulation_speed:'Simulation speed (×)',clearance_m:'Clearance at each end (m)',signal_block_m:'Assumed maximum signal block length (m)',authority_lookahead_m:'Minimum reservation lookahead (m; expands for braking)',starvation_s:'FIFO waiting threshold (s)',retention_hours:'History retention (hours)',quality_window_s:'Quality window (s)',random_incidents_per_hour:'Random incidents / simulation hour',random_seed:'Random seed',terminal_release_s:'Terminal withdrawal delay (s)',auto_dispatch:'Automatic meets, receiving tracks and overtakes'};
function renderSettings(){if(!config)return;$('settings-fields').innerHTML=Object.entries(config).filter(([k])=>k!=='weights').map(([k,v])=>typeof v==='boolean'?`<label>${label(settingLabels[k]||k)}<input name="${k}" type="checkbox" ${v?'checked':''} ${user.role!=='admin'?'disabled':''}></label>`:`<label>${label(settingLabels[k]||k)}<input name="${k}" type="number" step="any" value="${v}" required ${user.role!=='admin'?'disabled':''}></label>`).join('')+Object.entries(config.weights).map(([k,v])=>`<label><span data-weight-label="${k}">${esc(tr('Dispatch weight · {factor}',{factor:tr(k)}))}</span><input name="weight_${k}" type="number" min="0" max="20" step="0.1" value="${v}" required ${user.role!=='admin'?'disabled':''}></label>`).join('');$('settings-form').querySelector('button').disabled=user.role!=='admin';}
$('settings-form').onsubmit=async e=>{e.preventDefault();const fields=Object.fromEntries(new FormData(e.target)),data={weights:{}};Object.entries(fields).forEach(([k,v])=>{if(k.startsWith('weight_'))data.weights[k.slice(7)]=+v;else if(typeof config[k]!=='boolean')data[k]=+v;});Object.keys(config).filter(k=>typeof config[k]==='boolean').forEach(k=>data[k]=e.target.elements[k].checked);const result=await action('/api/config',data,'PUT');if(result){config=result;notify('Configuration saved. Dispatch plan reconsidered.');}};
window.addEventListener('resize',()=>{if(displayState)render(displayState,performance.now());});

function renderStationOptions(){
  if(!network)return;
  const d=network.demo;
  $('station-options').innerHTML=[{vertex:d.origin,name:tr('Demo west boundary')},{vertex:d.destination,name:tr('Demo east boundary')},...network.stations].map(s=>`<option value="${s.vertex}">${esc(s.name)}${s.name_en?' / '+esc(s.name_en):''}</option>`).join('');
}
window.addEventListener('railflow:languagechange',()=>{
  // Translate presentation in place: preserve drafts, filters, replay and the stream.
  [$('page-name').textContent,$('page-title').textContent,$('page-subtitle').textContent]=titles[currentTab].map(source=>tr(source));
  connectionText(connectionSource,connectionParams);
  transientMessages.forEach(({source,params},id)=>translatedText(id,source,params));
  $('incident-asset-type').querySelectorAll('option').forEach(option=>option.textContent=tr(option.value));
  document.querySelectorAll('[data-weight-label]').forEach(el=>el.textContent=tr('Dispatch weight · {factor}',{factor:tr(el.dataset.weightLabel)}));
  renderStationOptions();updateAssets();
  if(displayState)render(displayState,performance.now());
  if(logRows.length||currentTab==='history')renderLogs();
  if($('info-dialog').open)renderInfo();
  $('play-replay').textContent=tr(replayTimer?'Ⅱ Pause':'▶ Play');
  if(replayFrames.length){const s=replayFrames[+$('replay-slider').value];if(s)$('replay-time').textContent=tr('{time} · read only',{time:clock(s.sim_time)});}
});
