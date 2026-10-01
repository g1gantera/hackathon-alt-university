import type {Snapshot} from './types';

// Opt-in local acceptance diagnostics. No telemetry is sent anywhere.
// Two animation frames measure a browser paint opportunity, not physical display latency.
const enabled=typeof window!=='undefined'&&new URLSearchParams(window.location.search).get('performance')==='1';
type Sample={received:number;reported:Set<string>};
const samples=new WeakMap<Snapshot,Sample>();
const paints=new Map<string,{snapshot:Snapshot;received:number;surface:string;entity:string}>();
let remaining=2000;
function report(record:Record<string,unknown>){
 if(enabled&&remaining-->0)console.info('railflow.performance '+JSON.stringify(record));
}
function afterPaintOpportunity(callback:()=>void){
 requestAnimationFrame(()=>requestAnimationFrame(callback));
}

export function receivedSnapshot(snapshot:Snapshot,received:number){
 if(enabled)samples.set(snapshot,{received,reported:new Set()});
}

// Element Timing is a browser-reported rendering timestamp for visible text.
// A new annotated node is used for each diagnostic sample because this API
// reports the first paint of a node. Normal-mode nodes are never replaced.
export function paintIdentifier(snapshot:Snapshot,surface:'dashboard'|'map',entity='quality'):string|undefined {
 if(!enabled)return;
 const sample=samples.get(snapshot);
 if(!sample)return;
 const id=`railflow:${snapshot.epoch}:${snapshot.realtime?.seq}:${surface}:${entity}`;
 paints.set(id,{snapshot,received:sample.received,surface,entity});
 while(paints.size>1000)paints.delete(paints.keys().next().value!);
 return id;
}

export function paintAttributes(snapshot:Snapshot,surface:'dashboard'|'map') {
 const identifier=paintIdentifier(snapshot,surface);
 return identifier?{elementtiming:identifier}:{};
}

export function committedSnapshot(snapshot:Snapshot,surface:'dashboard'|'map'){
 if(!enabled)return;
 const sample=samples.get(snapshot);
 if(!sample||sample.reported.has(surface))return;
 sample.reported.add(surface);
 const commitMs=performance.now()-sample.received;
 afterPaintOpportunity(()=>report({type:'snapshot',surface,seq:snapshot.realtime?.seq,
  epoch:snapshot.epoch,sim_time_s:snapshot.sim_time_s,incidents:snapshot.incidents.length,
  running:snapshot.running,replanning:snapshot.replanning,visibility:document.visibilityState,
  commit_ms:Math.round(commitMs*100)/100,paint_opportunity_ms:Math.round((performance.now()-sample.received)*100)/100}));
}

if(enabled){
 report({type:'environment',user_agent:navigator.userAgent,logical_processors:navigator.hardwareConcurrency,
  viewport:[window.innerWidth,window.innerHeight],visibility:document.visibilityState,
  element_timing_supported:PerformanceObserver.supportedEntryTypes.includes('element')});
 if(PerformanceObserver.supportedEntryTypes.includes('element')){
  new PerformanceObserver(list=>{
   for(const entry of list.getEntries()){
    const paint=entry as PerformanceEntry&{identifier:string;renderTime:number;intersectionRect:DOMRectReadOnly};
    const pending=paints.get(paint.identifier);
    if(!pending||!paint.renderTime||paint.intersectionRect.width<=0||paint.intersectionRect.height<=0)continue;
    paints.delete(paint.identifier);
    const {snapshot,received,surface,entity}=pending;
    report({type:'visible_paint',surface,entity,seq:snapshot.realtime?.seq,epoch:snapshot.epoch,
     sim_time_s:snapshot.sim_time_s,incidents:snapshot.incidents.length,running:snapshot.running,
     visibility:document.visibilityState,visible_area_px:paint.intersectionRect.width*paint.intersectionRect.height,
     render_ms:Math.round((paint.renderTime-received)*100)/100});
   }
  }).observe({type:'element',buffered:true});
 }
 // Observe real controls during load without adding a diagnostic UI to the dashboard.
 window.addEventListener('click',event=>{
  const received=performance.now();
  const target=(event.target as Element|null)?.closest('button,[role="tab"],input');
  if(!target)return;
  afterPaintOpportunity(()=>report({type:'interaction',control:target.getAttribute('aria-label')||target.textContent?.trim().slice(0,60),
   visibility:document.visibilityState,paint_opportunity_ms:Math.round((performance.now()-received)*100)/100}));
 },{capture:true});
}
