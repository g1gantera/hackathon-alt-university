// Real Firefox/WebDriver BiDi render smoke. Run only against the disposable
// era test server on 127.0.0.1:8002: this loads and resets a demo on that server.
// Uses an isolated browser profile and writes screenshots beneath .build/.
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=new URL(process.env.RAIL_3D_TEST_URL||'http://127.0.0.1:8002');
if(!['127.0.0.1','localhost'].includes(base.hostname)||base.port!=='8002')throw Error('Use the isolated local test server on port 8002.');
const firefox=process.env.RAIL_FIREFOX||(existsSync('/snap/firefox/current/usr/lib/firefox/firefox')?'/snap/firefox/current/usr/lib/firefox/firefox':'firefox');
const output=path.join(root,'.build/scene3d-browser');
await fs.mkdir(output,{recursive:true});
const profile=await fs.mkdtemp(path.join(root,'.build/scene3d-firefox-'));
await fs.writeFile(path.join(profile,'user.js'),[
 'user_pref("webgl.disabled", false);',
 'user_pref("webgl.force-enabled", true);',
 'user_pref("gfx.webrender.all", true);',
 'user_pref("gfx.webrender.software", true);',
].join('\n'));
const debugPort=19223;
const stationsOnly=process.argv.includes('--stations-only');
let child,ws,context,loggedIn=false,id=0;
const pending=new Map(),errors=[],browserLog=[];
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function call(method,params={}){
 const key=++id;
 const result=new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{pending.delete(key);reject(Error(`BiDi timeout: ${method}`));},20000);
  pending.set(key,message=>{clearTimeout(timer);message.type==='error'?reject(Error(JSON.stringify(message))):resolve(message.result);});
 });
 ws.send(JSON.stringify({id:key,method,params}));
 return result;
}
async function evaluate(expression){
 const result=await call('script.evaluate',{expression,target:{context},awaitPromise:true});
 if(result.type==='exception')throw Error(result.exceptionDetails.text);
 return result.result.value;
}
async function waitFor(expression,timeout=25000){
 const deadline=Date.now()+timeout;
 while(Date.now()<deadline){if(await evaluate(expression))return;await delay(100);}
 throw Error(`Timed out: ${expression}`);
}
const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
const frame='document.querySelector("#rail-map").contentWindow';
const frameDocument=`${frame}.document`;
async function screenshot(name,clip=false){
 const options={context,origin:'document'};
 if(clip){
  const box=JSON.parse(await evaluate('JSON.stringify((()=>{const r=document.querySelector(".map-wrapper").getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};})())'));
  options.clip={type:'box',...box};
 }
 const image=await call('browsingContext.captureScreenshot',options);
 await fs.writeFile(path.join(output,name),Buffer.from(image.data,'base64'));
}
async function diagnostic(){
 return JSON.parse(await evaluate(`JSON.stringify((()=>{
  const f=${frame},canvas=f.document.querySelector('canvas');
  const gl=canvas&&(canvas.getContext('webgl2')||canvas.getContext('webgl'));
  return {frameURL:f.location.href,title:f.document.title,status:f.document.querySelector('#scene-status')?.textContent,
   loading:f.document.querySelector('#loading')?.textContent,loadingHidden:f.document.querySelector('#loading')?.hidden,
   canvas:canvas?{width:canvas.width,height:canvas.height}:null,
   webgl:gl?{lost:gl.isContextLost(),width:gl.drawingBufferWidth,height:gl.drawingBufferHeight,version:gl.getParameter(gl.VERSION)}:null,
   renderer:f.railSceneMetrics||null,selected:typeof selected==='undefined'?null:selected,
   live:typeof liveState==='undefined'?null:{version:liveState?.version,time:liveState?.sim_time,running:liveState?.running,trains:liveState?.trains?.length,
    trainHeads:liveState?.trains?.map(t=>({id:t.id,distance:t.distance_m,position:t.position,edge:t.edge,speed:t.speed_kmh}))},
   body:f.document.body.innerText.slice(-2000)};
 })())`));
}
async function captureStations(){
 const stationCameras=[];
 for(const [name,pattern] of [['kokshetau','Kokshetau|Кокшетау|Көкшетау'],['astana','Astana-1|Астана-1']]){
  const station=await evaluate(`(()=>{const f=${frame},d=f.document,match=Array.from(d.querySelectorAll('#station-places option')).find(o=>new RegExp(${JSON.stringify(pattern)},'i').test(o.value));if(!match)throw Error('Station camera option missing');const input=d.querySelector('#station-search');input.value=match.value;input.dispatchEvent(new f.Event('change',{bubbles:true}));return match.value;})()`);
  await delay(600);await screenshot(`station-${name}.png`,true);
  stationCameras.push({name,station});
 }
 return stationCameras;
}

try{
 const health=await fetch(new URL('/health',base));if(!health.ok)throw Error('Isolated server is not healthy');
 child=spawn(firefox,['--headless','--no-remote','--remote-debugging-port',String(debugPort),'--profile',profile,'about:blank'],
  {stdio:['ignore','pipe','pipe'],env:{...process.env,LIBGL_ALWAYS_SOFTWARE:'1',MOZ_WEBRENDER:'1'}});
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Firefox launch timeout')),20000);
  const onData=data=>{const text=data.toString();browserLog.push(text);if(text.includes('WebDriver BiDi listening')){clearTimeout(timer);resolve();}};
  child.stdout.on('data',onData);child.stderr.on('data',onData);child.on('error',error=>{clearTimeout(timer);reject(error);});
 });
 ws=new WebSocket(`ws://127.0.0.1:${debugPort}/session`);
 ws.onmessage=event=>{
  const message=JSON.parse(event.data);
  if(message.id){pending.get(message.id)?.(message);pending.delete(message.id);}
  else if(message.method==='log.entryAdded'&&message.params.level==='error')errors.push({type:message.params.type,text:message.params.text});
 };
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 const session=await call('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
 context=(await call('browsingContext.getTree')).contexts[0].context;
 await call('session.subscribe',{events:['log.entryAdded']});
 await call('browsingContext.setViewport',{context,viewport:{width:1512,height:1100},devicePixelRatio:1});
 await call('browsingContext.navigate',{context,url:base.href,wait:'interactive'});
 await click('#login-form button');
 await waitFor('!document.querySelector("#application").hidden && window.railMetrics.events>0');loggedIn=true;
 await click('#load-demo');await waitFor('document.querySelectorAll(".train-row").length===2');
 await click('.scene3d-view-switch button[aria-label="Low-poly railway view"]');
 await waitFor(`${frameDocument}.querySelector('#viewport canvas') && ${frameDocument}.querySelector('#loading')?.hidden && ${frame}.railSceneMetrics?.frames>0`,45000);
 const initial=await diagnostic();
 if(!initial.webgl||initial.webgl.lost||initial.webgl.width<100||initial.webgl.height<100)throw Error('3D view has no usable WebGL drawing buffer');
 await delay(500);
 await screenshot('scene-initial.png',true);
 await screenshot('dashboard-3d.png');
 if(stationsOnly){
  const result={status:'passed',checks:'station-cameras',stationCameras:await captureStations(),errors};
  await fs.writeFile(path.join(output,'station-results.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));
  if(errors.some(error=>error.type==='javascript'))throw Error('Station render reported script errors');
 }else{
 await click('#fit-trains');await click('#map-controls');
 await waitFor(`!${frameDocument}.querySelector('#layers').hidden`);
 await click('#map-controls');
 // Exercise ordinary orbit-control wheel input, then follow the selected train
 // so the moving screenshot shows model detail rather than an empty old camera.
 await evaluate(`(()=>{const f=${frame},canvas=f.document.querySelector('canvas');for(let i=0;i<28;i++)canvas.dispatchEvent(new f.WheelEvent('wheel',{deltaY:-100,bubbles:true,cancelable:true,clientX:f.innerWidth/2,clientY:f.innerHeight/2}));})()`);
 await delay(600);await screenshot('scene-closeup.png',true);
 await evaluate(`${frameDocument}.querySelector('#follow').click()`);
 await waitFor(`${frameDocument}.querySelector('#follow').getAttribute('aria-pressed')==='true'`);
 await evaluate('document.querySelector("#speed").value="120";document.querySelector("#speed").dispatchEvent(new Event("change"))');
 await waitFor('liveState.simulation_speed===120');
 await click('#start');await waitFor('liveState.sim_time>=180');
 await click('#pause');await waitFor('!liveState.running');await delay(700);
 await screenshot('scene-moving.png',true);
 const moving=await diagnostic();
 // The unchanged era passing scenario has a mapped 9.4-degree bend at
 // chainage 8845.2 m on E47073. Observe the real train while it straddles it.
 await evaluate('document.querySelector("#speed").value="30";document.querySelector("#speed").dispatchEvent(new Event("change"))');
 await waitFor('liveState.simulation_speed===30');
 await click('#start');await waitFor('liveState.sim_time>=430',20000);
 await click('#pause');await waitFor('!liveState.running');
 await evaluate('document.querySelector("#speed").value="10";document.querySelector("#speed").dispatchEvent(new Event("change"))');
 await waitFor('liveState.simulation_speed===10');
 await click('#start');await waitFor('liveState.trains.find(t=>t.id==="KZ-101").distance_m>=8870',20000);
 await click('#pause');await waitFor('!liveState.running');await delay(700);
 const curved=await diagnostic();
 await screenshot('scene-curve.png',true);
 await evaluate(`${frameDocument}.querySelector('#whole-train').click()`);
 await delay(400);await screenshot('scene-curve-whole.png',true);
 const stationCameras=await captureStations();
 await click('.train-row:nth-child(2)');await delay(200);
 const selected=await evaluate('selected');
 await click('.scene3d-view-switch button[aria-label="Geographic railway map"]');
 await waitFor(`${frame}.location.pathname==='/map-live' && typeof ${frame}.L!=='undefined'`);
 await click('.scene3d-view-switch button[aria-label="Low-poly railway view"]');
 await waitFor(`${frameDocument}.querySelector('#viewport canvas') && ${frameDocument}.querySelector('#loading')?.hidden && ${frame}.railSceneMetrics?.frames>0`,45000);
 if(await evaluate('selected')!==selected)throw Error('View switch changed selected train');
 await click('[data-tab="history"]');await click('#load-replay');
 await waitFor('!document.querySelector("#replay-banner").hidden');
 if(!await evaluate('document.querySelector("#start").disabled'))throw Error('Replay allowed simulator mutation');
 await click('#return-live');await waitFor('document.querySelector("#replay-banner").hidden');
 await click('[data-tab="overview"]');
 await call('browsingContext.setViewport',{context,viewport:{width:390,height:844},devicePixelRatio:1});
 await delay(300);await screenshot('scene-mobile.png');
 const overflow=await evaluate('document.documentElement.scrollWidth>innerWidth+1');
 const result={status:'passed',browser:session.capabilities.browserVersion,initial,moving,curved,stationCameras,
  same_iframe_view_toggle:true,selection_retained:true,replay_read_only:true,mobile_overflow:overflow,errors};
 await fs.writeFile(path.join(output,'results.json'),JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result,null,2));
 if(overflow||errors.some(error=>error.type==='javascript'))throw Error('Browser validation reported layout/script errors');
 }
}catch(error){
 const state=context?await diagnostic().catch(failure=>({diagnosticError:failure.message})):null;
 await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({error:error.message,state,errors,browserLog},null,2)+'\n');
 if(context)await screenshot('failure.png').catch(()=>{});
 throw error;
}finally{
 if(loggedIn&&context)await evaluate('fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"reset"})}).then(()=>null)').catch(()=>{});
 if(ws?.readyState===1){await call('session.end').catch(()=>{});ws.close();}
 child?.kill();
}
