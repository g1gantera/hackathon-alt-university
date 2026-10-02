// Real Firefox/WebDriver BiDi regression coverage for language switching.
// RAIL_TEST_URL must point to an isolated demo server: this loads a scenario and creates one incident.
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';

const root=path.resolve(import.meta.dirname,'..');
const base=process.env.RAIL_TEST_URL||'http://127.0.0.1:8012';
const firefox=process.env.RAIL_FIREFOX||'/snap/firefox/current/usr/lib/firefox/firefox';
const debugPort=Number(process.env.RAIL_BROWSER_DEBUG_PORT||19238);
const output=path.join(root,'.build/i18n-browser');
await fs.mkdir(output,{recursive:true});
const profile=await fs.mkdtemp(path.join(output,'profile-'));
const checks=[],errors=[],warnings=[];
let child,ws,context,id=0;
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
async function waitFor(expression,timeout=20000){
  const end=Date.now()+timeout;
  while(Date.now()<end){if(await evaluate(expression))return true;await new Promise(resolve=>setTimeout(resolve,100));}
  throw Error(`Timed out: ${expression}`);
}
const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
async function check(name,expression){const value=await evaluate(expression);record(name,value===true,value);}
async function language(locale){
  await evaluate(`(()=>{const select=document.querySelector('#application').hidden?document.querySelector('#login-screen [data-language-select]'):document.querySelector('#application [data-language-select]');select.value=${JSON.stringify(locale)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await waitFor(`document.documentElement.lang===${JSON.stringify(locale)}`);
  await check(`${locale}: all selectors synchronized`,`[...document.querySelectorAll('[data-language-select]')].every(select=>select.value===${JSON.stringify(locale)})`);
}
async function shot(name){const image=await call('browsingContext.captureScreenshot',{context,origin:'document'});await fs.writeFile(path.join(output,name+'.png'),Buffer.from(image.data,'base64'));}
async function overflow(name){
  const dimensions=await data(`({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,offenders:[...document.querySelectorAll('body *')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1&&!el.closest('.table-scroll')}).map(el=>({tag:el.tagName,id:el.id,class:el.className,right:Math.round(el.getBoundingClientRect().right)})).slice(0,18)})`);
  record(name,dimensions.scrollWidth<=dimensions.width+1,dimensions);
}
let fatal;
try{
  for(const resource of ['i18n.js','i18n-static.js','i18n-dynamic.js','i18n-messages.js','i18n-map.js']){
    const response=await fetch(`${base}/static/${resource}`);
    if(!response.ok)throw Error(`Localization resource unavailable: ${resource} (${response.status})`);
  }
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
  context=(await call('browsingContext.getTree')).contexts[0].context;
  await call('session.subscribe',{events:['log.entryAdded'],contexts:[context]});
  await call('browsingContext.setViewport',{context,viewport:{width:1512,height:1100},devicePixelRatio:1});
  await call('browsingContext.navigate',{context,url:base,wait:'interactive'});
  await call('browsingContext.activate',{context});
  await waitFor(`window.RailI18n&&document.querySelector('#login-form button')`);
  await check('fresh profile defaults to English',`document.documentElement.lang==='en'&&document.querySelector('#login-form button').textContent.includes('Enter dispatch control')`);
  await check('KZ / RU / ENG language labels',`JSON.stringify([...document.querySelector('#login-screen [data-language-select]').options].map(o=>o.textContent))==='["KZ","RU","ENG"]'`);
  await language('kk');
  await check('Kazakh login text and accessible language label',`document.querySelector('#login-form').textContent.includes('Пайдаланушы аты')&&document.querySelector('[data-language-select]').getAttribute('aria-label')==='Тіл'`);
  await shot('login-kk');
  await language('ru');
  await check('Russian login text',`document.querySelector('#login-form').textContent.includes('Имя пользователя')&&document.querySelector('[data-language-select]').getAttribute('aria-label')==='Язык'`);
  await call('browsingContext.reload',{context,wait:'interactive'});
  await waitFor(`window.RailI18n&&document.documentElement.lang==='ru'`);
  await check('language choice persists across reload',`localStorage.getItem('railflow.language')==='ru'&&document.querySelector('#login-form').textContent.includes('Имя пользователя')`);
  await evaluate(`document.querySelector('#login-form [name=username]').value='admin';document.querySelector('#login-form [name=password]').value='demo-admin'`);
  await click('#login-form button');
  await waitFor(`!document.querySelector('#application').hidden&&window.railMetrics.events>0`);
  await click('#load-demo');
  await waitFor(`liveState?.trains.length===2&&document.querySelectorAll('.train-row').length===2`);
  await waitFor(`!document.querySelector('#alert').hidden&&document.querySelector('#alert').textContent.includes('Сценарий')`);
  await evaluate(`document.querySelector('#speed').value='120';document.querySelector('#speed').dispatchEvent(new Event('change'))`);
  await waitFor(`liveState.simulation_speed===120`);
  await click('#start');
  await waitFor(`liveState.sim_time>=180`,30000);
  await click('#pause');
  await waitFor(`!liveState.running&&Object.keys(displayState.quality.factors).length>0`);
  await check('Russian scenario feedback',`document.querySelector('#alert').textContent.includes('Сценарий')`);
  await check('admin settings fields editable',`!document.querySelector('#settings-fields input').disabled`);
  await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n`,45000);
  await evaluate(`(()=>{const frame=document.querySelector('#rail-map').contentDocument;const basemap=frame.querySelector('#base');if(basemap){basemap.value='none';basemap.dispatchEvent(new Event('change'));}})()`);

  const formSnapshot=`(()=>{const ids=['train-form','incident-form','settings-form'];return Object.fromEntries(ids.map(id=>[id,[...document.getElementById(id).querySelectorAll('input,select,textarea')].map(el=>[el.name,el.value,el.checked])]))})()`;
  await click('[data-tab="incidents"]');
  await evaluate(`document.querySelector('#incident-kind').value='speed_restriction';document.querySelector('#incident-kind').dispatchEvent(new Event('change'));document.querySelector('#incident-form [name=note]').value='Draft incident stays';document.querySelector('#incident-form [name=duration_s]').value='345';document.querySelector('#incident-asset').value='47083'`);
  await click('[data-tab="settings"]');
  await evaluate(`document.querySelector('#settings-form [name=normal_threshold]').value='73';document.querySelector('#settings-form input[name^=weight_]').value='2.3'`);
  await click('#add-train-open');
  await evaluate(`document.querySelector('#train-form [name=id]').value='KZ-DRAFT';document.querySelector('#train-form [name=name]').value='Draft service';document.querySelector('#train-form [name=importance]').value='8';document.querySelector('#train-form [name=acceleration_mps2]').value='0.65'`);
  await click('[data-close="train-dialog"]');
  const drafts=await data(formSnapshot);
  const fixedValues=await data(`Object.fromEntries(['scenario','train-filter','incident-kind','incident-asset-type','log-kind'].map(id=>[id,[...document.getElementById(id).options].map(o=>o.value)]))`);
  for(const locale of ['ru','kk','en']){
    await click('[data-tab="history"]');
    await language(locale);
    await check(`${locale}: current tab preserved`,`currentTab==='history'&&document.querySelector('#tab-history').classList.contains('active')`);
    const currentDrafts=await data(formSnapshot);
    const draftDiff=Object.entries(drafts).flatMap(([form,fields])=>fields.flatMap((field,index)=>JSON.stringify(field)===JSON.stringify(currentDrafts[form][index])?[]:[{form,before:field,after:currentDrafts[form][index]}]));
    record(`${locale}: train, incident and settings drafts preserved`,draftDiff.length===0,draftDiff);
    const values=await data(`Object.fromEntries(['scenario','train-filter','incident-kind','incident-asset-type','log-kind'].map(id=>[id,[...document.getElementById(id).options].map(o=>o.value)]))`);
    record(`${locale}: API and filter values unchanged`,JSON.stringify(values)===JSON.stringify(fixedValues));
    await click('[data-tab="overview"]');
    await check(`${locale}: dynamic train states localized`,`[...document.querySelectorAll('.train-row')].every(row=>{const train=displayState.trains.find(t=>t.id===row.dataset.train);const source=train.state.replaceAll('_',' '),display=row.querySelector('.status-pill').textContent;return display===RailI18n.t(source)&&(${JSON.stringify(locale)}==='en'||display!==source);})`);
    await check(`${locale}: quality status and factor labels localized`,`document.querySelector('#quality-status').textContent===RailI18n.message(displayState.quality.status)&&document.querySelector('#quality-factors .factor span').textContent===RailI18n.t('Schedule adherence')&&(${JSON.stringify(locale)}==='en'||document.querySelector('#quality-factors .factor span').textContent!=='Schedule adherence')`);
    await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n.language===${JSON.stringify(locale)}`);
    await check(`${locale}: 2D map language propagated`,`document.querySelector('#rail-map').contentDocument.documentElement.lang===${JSON.stringify(locale)}`);
    await check(`${locale}: 2D route action localized`,`(()=>{const w=document.querySelector('#rail-map').contentWindow;return w.document.querySelector('#rGo').textContent===w.RailI18n.t('Find route')&&(${JSON.stringify(locale)}==='en'||w.document.querySelector('#rGo').textContent!=='Find route');})()`);
    await shot(`dashboard-${locale}`);
  }

  // Exercise a real translated incident submission and inspect the unchanged request protocol.
  await language('kk');
  await click('[data-tab="incidents"]');
  await evaluate(`window.__i18nRequests=[];const realFetch=window.fetch;window.fetch=(input,options)=>{if(String(input).includes('/api/incidents')&&options?.method==='POST')window.__i18nRequests.push(JSON.parse(options.body));return realFetch(input,options);};document.querySelector('#incident-kind').value='train_delay';document.querySelector('#incident-kind').dispatchEvent(new Event('change'));document.querySelector('#incident-asset').value=liveState.trains[0].id;document.querySelector('#incident-form [name=duration_s]').value='30';document.querySelector('#incident-form [name=note]').value='i18n browser regression'`);
  await click('#incident-form button[type="submit"]');
  await waitFor(`window.__i18nRequests.length===1&&liveState.incidents.some(i=>i.note==='i18n browser regression')`);
  await check('Kazakh incident submits stable API identifiers',`window.__i18nRequests[0].kind==='train_delay'&&window.__i18nRequests[0].asset_type==='train'`);

  await click('[data-tab="overview"]');
  await click('.scene3d-view-switch button:nth-child(2)');
  await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n&&document.querySelector('#rail-map').contentDocument.querySelector('#whole-train')`,45000);
  for(const locale of ['kk','ru','en']){
    await language(locale);
    await waitFor(`document.querySelector('#rail-map').contentWindow.RailI18n.language===${JSON.stringify(locale)}`);
    await check(`${locale}: 3D language and controls propagated`,`(()=>{const w=document.querySelector('#rail-map').contentWindow;return w.document.documentElement.lang===${JSON.stringify(locale)}&&w.document.querySelector('#whole-train').textContent===w.RailI18n.t('Whole train')&&(${JSON.stringify(locale)}==='en'||w.document.querySelector('#whole-train').textContent!=='Whole train');})()`);
    await check(`${locale}: 3D iframe accessible title localized`,`document.querySelector('#rail-map').title===RailI18n.t('3D railway view with live simulation overlays')`);
  }
  await shot('scene3d-en');
  await click('.scene3d-view-switch button:first-child');
  await waitFor(`document.querySelector('#rail-map').contentDocument.querySelector('#base')&&document.querySelector('#rail-map').contentWindow.RailI18n`,45000);
  await call('browsingContext.setViewport',{context,viewport:{width:390,height:844},devicePixelRatio:1});
  for(const locale of ['kk','ru','en']){
    await language(locale);
    await click('[data-tab="overview"]');
    await overflow(`${locale}: mobile dashboard fits viewport`);
    await shot(`mobile-${locale}`);
    await click('[data-tab="incidents"]');
    await overflow(`${locale}: mobile incidents fit viewport`);
    await click('[data-tab="history"]');
    await overflow(`${locale}: mobile history fits viewport`);
    await click('[data-tab="settings"]');
    await overflow(`${locale}: mobile settings fit viewport`);
  }
  await click('#logout');
  for(const locale of ['kk','ru','en']){
    await language(locale);
    await overflow(`${locale}: mobile login fits viewport`);
  }
  record('no unexpected JavaScript errors',errors.length===0,errors);
  console.log(`Firefox ${session.capabilities.browserVersion}: ${checks.filter(c=>c.pass).length}/${checks.length} checks passed`);
}catch(error){
  fatal=error.message;console.error(error);
  if(context&&ws?.readyState===WebSocket.OPEN){
    await shot('failure').catch(()=>{});
    await fs.writeFile(path.join(output,'failure-state.json'),JSON.stringify(await data(`({url:location.href,lang:document.documentElement.lang,text:document.body.innerText.slice(-4000),state:typeof liveState==='undefined'?null:liveState,errors:window.__i18nRequests})`).catch(()=>({})),null,2));
  }
}finally{
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({base,checks,errors,warnings,...(fatal?{fatal}:{})},null,2)+'\n');
  if(ws?.readyState===WebSocket.OPEN){try{await call('session.end');}catch{}ws.close();}
  if(child){child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else{child.once('exit',resolve);setTimeout(resolve,3000);}});}
  await fs.rm(profile,{recursive:true,force:true}).catch(()=>{});
}
if(fatal||checks.some(check=>!check.pass))process.exitCode=1;
