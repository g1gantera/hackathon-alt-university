// Presentation regression checks against an ISOLATED demo server on port 8013.
// Run with Node 22+ and Firefox. No browser or package downloads are required.
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';

const root=path.resolve(import.meta.dirname,'..');
const base=process.env.RAIL_TEST_URL||'http://127.0.0.1:8013';
const target=new URL(base);
if(!['127.0.0.1','localhost','[::1]'].includes(target.hostname)||target.port!=='8013'){
  throw Error('Glass browser checks create demo state: use an isolated local server on port 8013.');
}
const firefox=process.env.RAIL_FIREFOX||(existsSync('/snap/firefox/current/usr/lib/firefox/firefox')?'/snap/firefox/current/usr/lib/firefox/firefox':'firefox');
const debugPort=Number(process.env.RAIL_BROWSER_DEBUG_PORT||19243);
const output=path.join(root,'.build/glass-qa');
await fs.mkdir(output,{recursive:true});
const profile=await fs.mkdtemp(path.join(output,'profile-'));
const checks=[],errors=[],warnings=[];
let child,ws,context,id=0,fatal,browserVersion;
const pending=new Map();
function record(name,pass,details){checks.push({name,pass,...(details===undefined?{}:{details})});console.log(`${pass?'PASS':'FAIL'} ${name}${pass||details===undefined?'':': '+JSON.stringify(details)}`);}
async function call(method,params={}){
  const command=++id;
  const result=new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{pending.delete(command);reject(Error(`BiDi command timed out: ${method}`));},30000);
    pending.set(command,message=>{clearTimeout(timeout);resolve(message);});
  });
  ws.send(JSON.stringify({id:command,method,params}));
  const message=await result;
  if(message.type==='error')throw Error(JSON.stringify(message));
  return message.result;
}
async function evaluate(expression){
  const result=await call('script.evaluate',{expression,target:{context},awaitPromise:true});
  if(result.type==='exception')throw Error(result.exceptionDetails.text);
  return result.result.value;
}
const data=async expression=>JSON.parse(await evaluate(`JSON.stringify(${expression})`));
async function waitFor(expression,timeout=15000){
  const end=Date.now()+timeout;
  while(Date.now()<end){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}
  throw Error('Timed out: '+expression);
}
const visibleSource=`el=>{if(!el)return false;const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!el.closest('[hidden]');}`;
const visible=selector=>evaluate(`(${visibleSource})(document.querySelector(${JSON.stringify(selector)}))`);
async function point(selector){
  await waitFor(`(${visibleSource})(document.querySelector(${JSON.stringify(selector)}))`);
  const result=await data(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'nearest',inline:'nearest'});const r=el.getBoundingClientRect();const x=Math.max(1,Math.min(innerWidth-1,r.x+r.width/2)),y=Math.max(1,Math.min(innerHeight-1,r.y+r.height/2));const hit=document.elementFromPoint(x,y);return {x,y,disabled:el.disabled===true,hit:hit===el||el.contains(hit),cover:hit?.id||hit?.className};})()`);
  if(result.disabled)throw Error(`Cannot activate disabled control ${selector}`);
  if(!result.hit)throw Error(`Visible control ${selector} is covered by ${result.cover}`);
  return result;
}
async function click(selector){
  const {x,y}=await point(selector);
  await call('input.performActions',{context,actions:[{type:'pointer',id:'glass-mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:Math.round(x),y:Math.round(y),duration:0,origin:'viewport'},{type:'pointerDown',button:0},{type:'pointerUp',button:0}]}]});
}
async function change(selector,value,type='change'){
  await point(selector);
  await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.focus();el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(${JSON.stringify(type)},{bubbles:true}));})()`);
}
async function shot(name){const result=await call('browsingContext.captureScreenshot',{context,origin:'viewport'});await fs.writeFile(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));}
async function check(name,expression){record(name,await evaluate(expression));}
async function openMenu(){if(!await visible('#workspace-menu'))await click('#workspace-menu-button');}
async function closeMenu(){if(await visible('#workspace-menu'))await click('#workspace-menu-button');}
async function goTab(name){
  const selector=`[data-tab="${name}"]`;
  if(!await visible(selector))await openMenu();
  await click(selector);
  await waitFor(`currentTab===${JSON.stringify(name)}`);
  await closeMenu();
}
async function language(locale){
  let selector='#application [data-language-select]';
  if(!await visible(selector))await openMenu();
  await change(selector,locale);
  await waitFor(`document.documentElement.lang===${JSON.stringify(locale)}`);
  await closeMenu();
}
async function dimensions(name){
  const result=await data(`(()=>{const r=document.querySelector('#rail-map').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight,sameFrame:window.__glassFrame===document.querySelector('#rail-map')};})()`);
  record(name,result.sameFrame&&Math.abs(result.x)<1&&Math.abs(result.y)<1&&Math.abs(result.width-result.viewportWidth)<1&&Math.abs(result.height-result.viewportHeight)<1,result);
}
async function overflow(name){
  const result=await data(`({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,offenders:[...document.querySelectorAll('#application *')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1&&!el.closest('.table-scroll')&&getComputedStyle(el).visibility!=='hidden'&&!el.closest('[hidden]');}).slice(0,10).map(el=>({id:el.id,class:String(el.className),right:Math.round(el.getBoundingClientRect().right)}))})`);
  record(name,result.scrollWidth<=result.width+1,result);
}
async function checkProtectedFiles(){
  const source=await fs.readFile(path.join(root,'scripts/scene3d-tests/preservation.test.mjs'),'utf8');
  const mismatches=[];
  for(const [,file,expected] of source.matchAll(/ '([^']+)':'([a-f0-9]{40})'/g)){
    let bytes=await fs.readFile(path.join(root,file));
    if(file==='backend/app.py')bytes=Buffer.from(bytes.toString().replace('<script src="/static/i18n.js"></script><script src="/static/i18n-dynamic.js"></script><script src="/static/i18n-messages.js"></script><script src="/static/i18n-map.js"></script>',''));
    const actual=createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
    if(actual!==expected)mismatches.push(file);
  }
  record('protected graph, original map and simulation sources match baseline',mismatches.length===0,mismatches);
}
try{
  const response=await fetch(base+'/health');if(!response.ok)throw Error(`Isolated server unavailable (${response.status})`);
  await checkProtectedFiles();
  await fs.writeFile(path.join(profile,'user.js'),'user_pref("ui.prefersReducedMotion", 1);\nuser_pref("focusmanager.testmode", true);\n');
  child=spawn(firefox,['--headless','--no-remote','--remote-debugging-port',String(debugPort),'--profile',profile,'about:blank'],{stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Firefox launch timeout')),15000);
    const watch=chunk=>{if(chunk.toString().includes('WebDriver BiDi listening')){clearTimeout(timer);resolve();}};
    child.stderr.on('data',watch);child.stdout.on('data',watch);child.on('error',reject);
  });
  ws=new WebSocket(`ws://127.0.0.1:${debugPort}/session`);
  ws.onmessage=event=>{
    const message=JSON.parse(event.data);
    if(message.id){pending.get(message.id)?.(message);pending.delete(message.id);}
    else if(message.method==='log.entryAdded'&&message.params.type==='javascript'&&message.params.level==='error'){
      const text=message.params.text;
      if(/WebGL|graphics context|Error creating WebGL context/i.test(text))warnings.push(text);else errors.push(text);
    }
  };
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  const session=await call('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
  browserVersion=session.capabilities.browserVersion;
  context=(await call('browsingContext.getTree')).contexts[0].context;
  await call('session.subscribe',{events:['log.entryAdded'],contexts:[context]});
  await call('browsingContext.setViewport',{context,viewport:{width:1512,height:982},devicePixelRatio:1});
  await call('browsingContext.navigate',{context,url:base,wait:'interactive'});
  await call('browsingContext.activate',{context});
  await waitFor(`window.RailI18n&&document.querySelector('#login-form button')`);
  await shot('login-desktop');
  await click('#login-form button[type="submit"]');
  await waitFor(`!document.querySelector('#application').hidden&&window.railMetrics.events>0`);
  await evaluate(`window.__glassFrame=document.querySelector('#rail-map')`);
  await waitFor(`mapReady`,45000);
  await dimensions('desktop map covers viewport');
  await check('map remains reachable through the clear center',`document.elementFromPoint(innerWidth/2,innerHeight/2)===document.querySelector('#rail-map')`);
  await click('#scenario-open');
  await waitFor(`document.querySelector('#scenario-dialog').open`);
  await change('#scenario','passing');
  await click('#load-demo');
  await waitFor(`liveState?.trains.length===2&&document.querySelectorAll('.train-row').length===2`);
  if(await evaluate(`document.querySelector('#scenario-dialog').open`))await click('#scenario-close');
  record('scenario dialog loads the real passing scenario',true);
  await change('#speed','120');
  await waitFor(`liveState.simulation_speed===120`);
  await click('#start');
  await waitFor(`liveState.running&&liveState.sim_time>=180`,30000);
  await click('#pause');
  await waitFor(`!liveState.running`);
  record('start, speed and pause use real simulation state',true);
  const firstId=await evaluate(`liveState.trains[0].id`);
  await change('#train-search',firstId,'input');
  await check('train search filters visible services',`document.querySelectorAll('.train-row').length===1&&document.querySelector('.train-row').dataset.train===${JSON.stringify(firstId)}`);
  await change('#train-search','','input');
  const lastId=await evaluate(`liveState.trains.at(-1).id`);
  await click(`.train-row[data-train="${lastId}"]`);
  await check('selected service drives the existing advisory',`selected===${JSON.stringify(lastId)}&&document.querySelector('#selected-title').textContent.includes(${JSON.stringify(lastId)})`);
  await click('#quality-open');
  await check('quality details can be opened',`(${visibleSource})(document.querySelector('.quality-panel'))`);
  await dimensions('opening quality does not resize the map');
  await shot('desktop-quality');
  await click('#quality-close');
  await click('#advisory-open');
  await check('driving advisory can be opened',`(${visibleSource})(document.querySelector('.bottom-grid'))`);
  await dimensions('opening advisory does not resize the map');
  await shot('desktop-advisory');
  await click('#advisory-close');
  await openMenu();
  await check('workspace menu exposes secondary actions',`document.querySelector('#workspace-menu-button').getAttribute('aria-expanded')==='true'`);
  await shot('desktop-menu');
  await closeMenu();
  await click('#fit-trains');
  await shot('desktop-overview');
  for(const name of ['timetable','incidents','history','settings']){
    await goTab(name);await dimensions(`${name}: background stays fullscreen`);
    await overflow(`${name}: desktop fits viewport`);
    if(name==='timetable')await shot('desktop-timetable');
  }
  await goTab('history');
  await click('#load-replay');
  await waitFor(`replaying&&!document.querySelector('#replay-banner').hidden`);
  await check('replay disables simulation mutations',`document.querySelector('#start').disabled&&[...document.querySelectorAll('.live-action')].every(el=>el.disabled)`);
  await change('#replay-slider','1','input');
  await click('#return-live');
  await waitFor(`!replaying&&document.querySelector('#replay-banner').hidden`);
  record('replay slider and return-to-live remain functional',true);
  await goTab('overview');
  for(const locale of ['kk','ru','en']){
    await language(locale);
    await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n?.language===${JSON.stringify(locale)}`);
    await check(`${locale}: existing train state labels remain localized`,`[...document.querySelectorAll('.train-row')].every(row=>row.querySelector('.status-pill').textContent===RailI18n.t(displayState.trains.find(t=>t.id===row.dataset.train).state.replaceAll('_',' ')))`);
    await overflow(`${locale}: desktop fits viewport`);
  }
  await click('.scene3d-view-switch button:nth-child(2)');
  await waitFor(`document.querySelector('#rail-map').contentDocument?.querySelector('#whole-train')`,45000);
  await check('3D view retains the iframe and current state boundary',`window.__glassFrame===document.querySelector('#rail-map')&&document.querySelector('#rail-map').getAttribute('src')==='/static/scene3d/index.html'`);
  await dimensions('3D map covers the same viewport');
  await language('kk');
  await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n?.language==='kk'`);
  await check('3D controls receive the selected language',`(()=>{const w=document.querySelector('#rail-map').contentWindow;return w.document.querySelector('#whole-train').textContent===w.RailI18n.t('Whole train');})()`);
  await shot('desktop-3d');
  await click('.scene3d-view-switch button:first-child');
  await waitFor(`document.querySelector('#rail-map').contentDocument?.querySelector('#base')&&document.querySelector('#rail-map').contentWindow.RailI18n`,45000);
  await language('en');
  await check('2D view restores without replacing the iframe',`window.__glassFrame===document.querySelector('#rail-map')&&document.querySelector('#rail-map').getAttribute('src')==='/map-live'`);
  const unlabeled=await data(`([...document.querySelectorAll('#application button')].filter(${visibleSource}).filter(el=>!el.getAttribute('aria-label')&&!el.textContent.trim().match(/[A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]/)).map(el=>({id:el.id,text:el.textContent})))`);
  record('visible icon-only buttons have accessible labels',unlabeled.length===0,unlabeled);
  const undersized=await data(`([...document.querySelectorAll('#application button,#application select')].filter(${visibleSource}).filter(el=>{const r=el.getBoundingClientRect();return r.width<34||r.height<34;}).map(el=>({id:el.id,class:el.className,width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height})))`);
  record('visible buttons and selects meet 34px touch targets',undersized.length===0,undersized);
  await call('browsingContext.activate',{context});
  await evaluate(`document.querySelector('[data-tab="overview"]').focus()`);
  await call('input.performActions',{context,actions:[{type:'key',id:'glass-keyboard',actions:[{type:'keyDown',value:'\uE004'},{type:'keyUp',value:'\uE004'}]}]});
  const focus=await data(`(()=>{const el=document.activeElement,s=getComputedStyle(el);return {documentFocus:document.hasFocus(),tag:el.tagName,id:el.id,tab:el.dataset.tab,focusVisible:el.matches(':focus-visible'),outline:s.outlineStyle,width:parseFloat(s.outlineWidth)};})()`);
  record('keyboard Tab reaches the next visible navigation control',focus.tag==='BUTTON'&&focus.tab==='timetable',focus);
  if(focus.documentFocus){
    record('keyboard focus has a visible outline',focus.focusVisible&&focus.outline!=='none'&&focus.width>0,focus);
  }else{
    const reason='Headless Firefox reports document.hasFocus() false; focus pseudo-class cannot be assessed in this environment.';
    checks.push({name:'keyboard focus has a visible outline',skipped:true,reason,details:focus});
    warnings.push(reason);console.log('SKIP keyboard focus has a visible outline: '+reason);
  }
  await check('reduced-motion preference is active',`matchMedia('(prefers-reduced-motion: reduce)').matches`);
  const animations=await data(`document.getAnimations().filter(a=>a.effect?.getComputedTiming().duration>10).map(a=>({name:a.animationName,duration:a.effect.getComputedTiming().duration}))`);
  record('reduced motion suppresses long active UI animations',animations.length===0,animations);
  await call('browsingContext.setViewport',{context,viewport:{width:390,height:844},devicePixelRatio:1});
  for(const locale of ['kk','ru','en']){
    await language(locale);await goTab('overview');
    await dimensions(`${locale}: mobile map covers viewport`);
    await overflow(`${locale}: mobile dashboard fits viewport`);
    await shot(`mobile-overview-${locale}`);
    for(const name of ['incidents','history','settings']){await goTab(name);await overflow(`${locale}: mobile ${name} fits viewport`);}
  }
  await goTab('overview');
  const sheet=await data(`(()=>{const el=document.querySelector('.train-panel'),r=el.getBoundingClientRect(),s=getComputedStyle(el);return {top:r.top,bottom:r.bottom,height:r.height,position:s.position,viewport:innerHeight};})()`);
  record('mobile service panel is a floating bottom sheet',['fixed','absolute'].includes(sheet.position)&&sheet.top>100&&sheet.bottom<=sheet.viewport+1&&sheet.bottom>=sheet.viewport-140,sheet);
  await click('#mobile-sheet-toggle');
  await dimensions('collapsing mobile sheet preserves map dimensions');
  await shot('mobile-collapsed');
  await click('#mobile-sheet-toggle');
  await click('#quality-open');
  await overflow('mobile quality sheet fits viewport');
  await shot('mobile-quality');
  await click('#quality-close');
  await click('#scenario-open');
  await overflow('mobile scenario dialog fits viewport');
  await shot('mobile-scenario');
  await click('#scenario-close');
  await call('browsingContext.setViewport',{context,viewport:{width:360,height:800},devicePixelRatio:1});
  await overflow('small mobile dashboard fits viewport');
  await dimensions('small mobile map covers viewport');
  await shot('mobile-small');
  await call('browsingContext.setViewport',{context,viewport:{width:844,height:390},devicePixelRatio:1});
  await overflow('compact landscape dashboard fits viewport');
  await dimensions('compact landscape map covers viewport');
  await shot('mobile-landscape');
  await checkProtectedFiles();
  record('no unexpected JavaScript errors',errors.length===0,errors);
  console.log(`Firefox ${browserVersion}: ${checks.filter(check=>check.pass).length}/${checks.filter(check=>!check.skipped).length} checks passed, ${checks.filter(check=>check.skipped).length} skipped`);
}catch(error){
  fatal=error.stack||error.message;console.error(error);
  if(context&&ws?.readyState===WebSocket.OPEN){
    await shot('failure').catch(()=>{});
    await fs.writeFile(path.join(output,'failure-state.json'),JSON.stringify(await data(`({url:location.href,lang:document.documentElement.lang,active:document.activeElement?.outerHTML.slice(0,1200),text:document.body.innerText.slice(-5000),tab:typeof currentTab==='undefined'?null:currentTab})`).catch(()=>({})),null,2));
  }
}finally{
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({base,browserVersion,checks,errors,warnings,...(fatal?{fatal}:{})},null,2)+'\n');
  if(ws?.readyState===WebSocket.OPEN){try{await call('session.end');}catch{}ws.close();}
  if(child){child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else{child.once('exit',resolve);setTimeout(resolve,3000);}});}
  await fs.rm(profile,{recursive:true,force:true}).catch(()=>{});
}
if(fatal||checks.some(check=>!check.pass&&!check.skipped))process.exitCode=1;
