// System Firefox fallback: real WebDriver BiDi, plus a proxy for actual transport failure.
// Node 22+ and a local Firefox installation; no browser download or npm install required.
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
const root=path.resolve(import.meta.dirname,'..');
const firefox=process.env.RAIL_FIREFOX||(existsSync('/snap/firefox/current/usr/lib/firefox/firefox')?'/snap/firefox/current/usr/lib/firefox/firefox':'firefox');
const upstream=new URL(process.env.RAIL_TEST_URL||'http://127.0.0.1:8000');
const port=18011,debugPort=19222,base=`http://127.0.0.1:${port}`;
const sockets=new Set();let offline=false;
const proxy=http.createServer((req,res)=>{
  if(process.env.RAIL_BROWSER_DEBUG)console.log('request',req.method,req.url);
  if(offline){res.destroy();return;}
  const up=http.request({hostname:upstream.hostname,port:upstream.port||80,path:req.url,method:req.method,headers:req.headers},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});
  req.pipe(up);up.on('error',()=>res.destroy());res.on('close',()=>{up.destroy();if(process.env.RAIL_BROWSER_DEBUG)console.log('complete',req.url);});
});
proxy.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
await new Promise(r=>proxy.listen(port,'127.0.0.1',r));
await fs.mkdir(path.join(root,'.build'),{recursive:true});
const profile=await fs.mkdtemp(path.join(root,'.build/firefox-check-'));
const child=spawn(firefox,['--headless','--no-remote','--remote-debugging-port',String(debugPort),'--profile',profile,'about:blank'],{stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Firefox launch timeout')),15000);const check=b=>{if(b.toString().includes('WebDriver BiDi listening')){clearTimeout(timer);resolve();}};child.stderr.on('data',check);child.stdout.on('data',check);child.on('error',reject);});
const ws=new WebSocket(`ws://127.0.0.1:${debugPort}/session`);let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}else if(m.method==='log.entryAdded'&&m.params.type==='javascript'&&m.params.level==='error')errors.push(m.params.text);};
await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
async function call(method,params={}){const i=++id;const p=new Promise(r=>pending.set(i,r));ws.send(JSON.stringify({id:i,method,params}));const m=await p;if(m.type==='error')throw Error(JSON.stringify(m));return m.result;}
let context;
async function evaluate(expression){const r=await call('script.evaluate',{expression,target:{context},awaitPromise:true});if(r.type==='exception')throw Error(r.exceptionDetails.text);return r.result.value;}
async function waitFor(expression,timeout=15000){const end=Date.now()+timeout;while(Date.now()<end){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out: '+expression);}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function shot(name){const r=await call('browsingContext.captureScreenshot',{context,origin:'document'});await fs.writeFile(path.join(root,'artifacts',name),Buffer.from(r.data,'base64'));}
try{
  const session=await call('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
  context=(await call('browsingContext.getTree')).contexts[0].context;
  await call('session.subscribe',{events:['log.entryAdded'],contexts:[context]});
  await call('browsingContext.setViewport',{context,viewport:{width:1512,height:1100},devicePixelRatio:1});
  await call('browsingContext.navigate',{context,url:base,wait:'interactive'});
  await call('browsingContext.activate',{context});
  await click('#login-form button');
  await waitFor('!document.querySelector("#application").hidden && window.railMetrics.events > 0');
  await click('#load-demo');
  await waitFor('document.querySelectorAll(".train-row").length === 2');
  await evaluate('document.querySelector("#speed").value="120";document.querySelector("#speed").dispatchEvent(new Event("change"))');
  await waitFor('liveState.simulation_speed===120');
  await click('#start');
  await waitFor('liveState.sim_time >= 1000',30000);
  await click('#pause');
  await waitFor('!liveState.running && mapReady',45000);
  // Use the map's existing offline basemap option; do not change source rendering.
  await evaluate('document.querySelector("#rail-map").contentWindow.document.querySelector("#base").value="none";document.querySelector("#rail-map").contentWindow.document.querySelector("#base").dispatchEvent(new Event("change"))');
  await shot('dashboard.png');
  await evaluate('document.querySelector("#rail-map").contentWindow.eval("map.fitBounds(L.latLngBounds(E[47083].pts),{padding:[50,50],maxZoom:15,animate:false}); void 0")');
  await new Promise(r=>setTimeout(r,400));
  const bounds=JSON.parse(await evaluate('JSON.stringify((()=>{const r=document.querySelector(".map-wrapper").getBoundingClientRect();return {x:r.x,y:r.y+scrollY,width:r.width,height:r.height};})())'));
  const mapImage=await call('browsingContext.captureScreenshot',{context,origin:'document',clip:{type:'box',...bounds}});
  await fs.writeFile(path.join(root,'artifacts/map-demo.png'),Buffer.from(mapImage.data,'base64'));
  await click('#fit-trains');
  const before=await evaluate('window.railMetrics.events');
  offline=true;for(const socket of sockets)socket.destroy();
  await waitFor('document.querySelector("#connection").textContent.toLowerCase().includes("disconnect")');
  await new Promise(r=>setTimeout(r,1200));offline=false;
  await waitFor(`window.railMetrics.events > ${before} && document.querySelector("#connection").textContent.includes("Connected")`,25000);
  await click('[data-tab="incidents"]');await click('#incident-burst');
  await waitFor('document.querySelectorAll(".incident-item").length===10');
  await click('.clear-incident');await waitFor('document.querySelectorAll(".incident-item").length===9');
  await click('[data-tab="history"]');await click('#load-replay');
  await waitFor('!document.querySelector("#replay-banner").hidden');
  if(!await evaluate('document.querySelector("#start").disabled'))throw Error('Replay failed to disable mutations');
  await evaluate('replayFrame(10)');await click('#return-live');
  await waitFor('document.querySelector("#replay-banner").hidden');
  const csv=await evaluate('fetch("/api/report.csv").then(r=>r.text())');
  if(!csv.includes('incident')||!csv.includes('train_summary'))throw Error('Report missing evidence');
  await fs.writeFile(path.join(root,'artifacts/sample-report.csv'),csv);
  await click('[data-tab="timetable"]');await shot('timetable.png');
  await click('[data-tab="overview"]');
  const version=await evaluate('lastVersion');await evaluate('receive({...liveState,version:liveState.version-1})');
  if(await evaluate('lastVersion')<version)throw Error('Obsolete state replaced live state');
  const values=JSON.parse(await evaluate('JSON.stringify(window.railMetrics)'));
  const metrics=JSON.parse(await evaluate('fetch("/api/metrics").then(r=>r.text())'));
  await call('browsingContext.setViewport',{context,viewport:{width:390,height:844},devicePixelRatio:1});
  await shot('mobile.png');
  const overflow=await evaluate('document.documentElement.scrollWidth > innerWidth+1');
  const stats=a=>{a.sort((a,b)=>a-b);return {samples:a.length,median:+a[Math.floor(a.length/2)].toFixed(3),p95:+a[Math.max(0,Math.floor(a.length*.95)-1)].toFixed(3),max:+a.at(-1).toFixed(3)};};
  const result={browser:`Firefox ${session.capabilities.browserVersion} · WebDriver BiDi`,viewport:'1512×1100 / mobile 390×844, DPR 1',reconnect_verified:true,replay_read_only_verified:true,report_download_verified:true,out_of_order_discarded:values.discarded,received_events:values.events,browser_errors:errors,mobile_document_overflow:overflow,ui_receipt_to_render_ms:stats(values.renderMs),map_message_to_ack_ms:stats(values.mapLatencyMs),server_metrics:metrics};
  await fs.writeFile(path.join(root,'artifacts/browser-results.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));
  if(errors.length||overflow||result.ui_receipt_to_render_ms.p95>=500)throw Error('Browser validation failed');
}catch(error){
  console.error('Page errors:',errors);
  console.error(await evaluate('JSON.stringify({text:document.body.innerText.slice(-1500),metrics:window.railMetrics,ready:document.readyState,visible:document.visibilityState,mapReady,frameState:document.querySelector("#rail-map").contentDocument.readyState,frameHTML:document.querySelector("#rail-map").contentDocument.documentElement.outerHTML.slice(-1200),leaflet:typeof document.querySelector("#rail-map").contentWindow.L,rail:typeof document.querySelector("#rail-map").contentWindow.RAIL,resources:document.querySelector("#rail-map").contentWindow.performance.getEntriesByType("resource").map(r=>({name:r.name,duration:r.duration,bytes:r.transferSize}))})').catch(()=>''));
  await shot('browser-failure.png').catch(()=>{});
  throw error;
}finally{
  try{await call('session.end');}catch{}
  ws.close();child.kill();for(const s of sockets)s.destroy();proxy.close();
}
