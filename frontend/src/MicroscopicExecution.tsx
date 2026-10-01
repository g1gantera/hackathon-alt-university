import {useEffect,useRef,useState} from 'react';
import {Button,Modal,Select} from 'antd';
import maplibregl from 'maplibre-gl';
import {api} from './store';
import type {Snapshot} from './types';

type Position=[number,number]; // engine: latitude, longitude
type Moving={id:string;position:Position;speed_mps:number};
type Train={id:string;position:Position;speed_kmh:number;state:string;reason:string;edge:number;authority_m:number|null;distance_m:number;hold:null|{kind?:string;partner?:string;label?:string}};
type Trial={run_id:string;revision:number;sim_time:number;source:string;source_plan_id:string;trains:Train[];signals:{id:string;position:Position;aspect:string}[];incidents:{id:string;status:string;kind:string;asset_id:string}[];events:{id:string;sim_time:number;message:string}[];safety_errors:string[];deadlock:string[];geometry:{type:"FeatureCollection";features:{type:"Feature";properties:Record<string,string|number>;geometry:{type:"LineString";coordinates:number[][]}}[]};frames?:{time_s:number;trains:Moving[]}[];limitations:string[]};
const sources=[{value:'integrated',label:'Импортировать текущий график'},{value:'passing',label:'Проверка: встречное скрещение'},{value:'overtaking',label:'Проверка: обгон'},{value:'priority',label:'Проверка: приоритеты'},{value:'closure',label:'Проверка: объезд закрытия'}];
function ExecutionMap({trial}:{trial:Trial}){
 const container=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null),markers=useRef(new Map<string,maplibregl.Marker>()),latest=useRef(trial),frame=useRef(0);
 latest.current=trial;
 useEffect(()=>{
  const m=new maplibregl.Map({container:container.current!,style:{version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef1eb'}}]},center:[70,52],zoom:8});map.current=m;m.addControl(new maplibregl.NavigationControl());
  m.on('load',()=>{
   m.addSource('tracks',{type:'geojson',data:latest.current.geometry});m.addLayer({id:'tracks',type:'line',source:'tracks',paint:{'line-width':3,'line-color':['match',['get','category'],'siding','#20a89a','yard','#377df0','crossover','#f68b43','#344352']}});
   const bounds=new maplibregl.LngLatBounds();for(const f of latest.current.geometry.features){if(f.geometry.type==='LineString')for(const c of f.geometry.coordinates)bounds.extend(c as [number,number]);}if(!bounds.isEmpty())m.fitBounds(bounds,{padding:45,duration:0});
   m.addSource('signals',{type:'geojson',data:{type:'FeatureCollection',features:[]}});m.addLayer({id:'signals',type:'circle',source:'signals',paint:{'circle-radius':4,'circle-stroke-width':1,'circle-stroke-color':'white','circle-color':['match',['get','aspect'],'green','#119e63','yellow','#e9b52c','#dc3e37']}});
   draw(latest.current.trains.map(t=>({...t,speed_mps:t.speed_kmh/3.6})));
  });
  function draw(items:Moving[]){for(const t of items){let marker=markers.current.get(t.id);if(!marker){const el=document.createElement('div');el.style.cssText='background:#176e63;color:white;padding:3px 6px;border-radius:4px;font:12px sans-serif;white-space:nowrap';el.textContent=t.id;marker=new maplibregl.Marker({element:el,anchor:'bottom'}).setLngLat([t.position[1],t.position[0]]).addTo(m);markers.current.set(t.id,marker);}marker.setLngLat([t.position[1],t.position[0]]);}}
  return()=>{cancelAnimationFrame(frame.current);markers.current.forEach(marker=>marker.remove());markers.current.clear();m.remove();map.current=null;};
 },[trial.run_id]);
 useEffect(()=>{
  const m=map.current;if(!m)return;
  const render=(items:Moving[])=>{for(const t of items){let marker=markers.current.get(t.id);if(!marker){const el=document.createElement('div');el.style.cssText='background:#176e63;color:white;padding:3px 6px;border-radius:4px;font:12px sans-serif';el.textContent=t.id;marker=new maplibregl.Marker({element:el,anchor:'bottom'}).setLngLat([t.position[1],t.position[0]]).addTo(m);markers.current.set(t.id,marker);}marker.setLngLat([t.position[1],t.position[0]]);marker.getElement().title=`${t.id}: ${(t.speed_mps*3.6).toFixed(1)} км/ч`;}};
  if(m.getSource('tracks'))(m.getSource('tracks') as maplibregl.GeoJSONSource).setData(trial.geometry);
  if(m.getSource('signals'))(m.getSource('signals') as maplibregl.GeoJSONSource).setData({type:'FeatureCollection',features:trial.signals.map(s=>({type:'Feature',properties:{aspect:s.aspect},geometry:{type:'Point',coordinates:[s.position[1],s.position[0]]}}))});
  const frames=trial.frames||[];cancelAnimationFrame(frame.current);
  if(frames.length<2){render(trial.trains.map(t=>({...t,speed_mps:t.speed_kmh/3.6})));return;}
  const started=performance.now();function animate(now:number){const at=Math.min(frames.length-1,(now-started)/500*(frames.length-1)),i=Math.floor(at),a=frames[i],b=frames[Math.min(i+1,frames.length-1)],p=at-i;render(a.trains.map(t=>{const next=b.trains.find(x=>x.id===t.id)||t;return {...t,position:[t.position[0]+p*(next.position[0]-t.position[0]),t.position[1]+p*(next.position[1]-t.position[1])] as Position};}));if(at<frames.length-1)frame.current=requestAnimationFrame(animate);}frame.current=requestAnimationFrame(animate);
  return()=>cancelAnimationFrame(frame.current);
 },[trial]);
 return <div ref={container} style={{height:440,border:'1px solid #ccd7d4'}}/>;
}
export function MicroscopicExecution({snapshot,disabled}:{snapshot:Snapshot;disabled:boolean}){
 const [open,setOpen]=useState(false),[trial,setTrial]=useState<Trial|null>(null),[source,setSource]=useState('passing'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(15),[target,setTarget]=useState('');
 const current=useRef(trial);current.current=trial;
 useEffect(()=>{setPlaying(false);setTrial(null);setError('');},[snapshot.epoch]);
 const execute=async(path:string,body:unknown)=>{setBusy(true);setError('');try{const value=await api<Trial>(`/execution/${path}`,'POST',body);current.current=value;setTrial(value);if(!value.trains.some(t=>t.id===target))setTarget(value.trains[0]?.id||'');return true;}catch(e){setPlaying(false);setError((e as Error).message);return false;}finally{setBusy(false);}};
 const step=async(seconds:number)=>{const value=current.current;if(!value)return false;return execute('step',{run_id:value.run_id,revision:value.revision,seconds});};
 useEffect(()=>{if(!playing||!open||disabled)return;let cancelled=false,timer:ReturnType<typeof setTimeout>;async function tick(){if(cancelled)return;const ok=await step(Math.min(30,speed/2));if(!cancelled&&ok)timer=setTimeout(tick,500);}void tick();return()=>{cancelled=true;clearTimeout(timer);};},[playing,open,speed,disabled]);
 const mutateDisabled=disabled||busy||playing;
 return <section className="panel"><h3>Детальное движение · перенос era</h3><p>Проверка торможения, блоковых разрешений, скрещений и обгонов на рёбрах карты.</p><Button onClick={()=>setOpen(true)}>Открыть детальный режим</Button>
 <Modal title="Детальное исполнение · отдельный проверочный запуск" open={open} width="95vw" footer={null} onCancel={()=>{setPlaying(false);setOpen(false);}}>
 <p>Основная симуляция ставится на паузу при создании запуска. Этот режим не заменяет её график или показатели. Если закрыть окно, новые шаги не выполняются; уже отправленный шаг завершится.</p>
 <Select value={source} onChange={setSource} options={sources} style={{minWidth:290}} disabled={mutateDisabled}/><Button disabled={mutateDisabled} onClick={()=>execute('create',{epoch:snapshot.epoch,source})}>Создать запуск</Button>
 <Button disabled={busy} onClick={async()=>{try{const v=await api<Trial>('/execution/state');setTrial(v);current.current=v;setError('');}catch(e){setError((e as Error).message);}}}>Обновить состояние</Button>
 {error&&<p role="alert" style={{color:'#a4262c',whiteSpace:'pre-wrap'}}>{error}</p>}
 {trial&&<><p><strong>{sources.find(s=>s.value===trial.source)?.label}</strong> · время {trial.sim_time.toFixed(1)} с · {trial.trains.length} поездов · нарушений {trial.safety_errors.length}</p>
 <Button disabled={disabled||busy&&!playing} onClick={()=>setPlaying(!playing)}>{playing?'Пауза':'Запустить'}</Button><Button disabled={mutateDisabled} onClick={()=>step(1)}>Шаг 1 с</Button><Select value={speed} disabled={playing||busy} onChange={setSpeed} options={[1,5,15,30,60].map(value=>({value,label:`Темп ×${value}`}))}/><span> Шаг физики ≤0,2 с; при тяжёлом расчёте воспроизведение замедляется.</span>
 <ExecutionMap trial={trial}/>
 {trial.deadlock.length>0&&<p role="alert">Взаимное ожидание: {trial.deadlock.join(', ')}. Автоматическое разрешение всех тупиков не гарантируется.</p>}
 <div style={{overflow:'auto',maxHeight:260}}><table><thead><tr><th>Поезд</th><th>Скорость</th><th>Пройдено</th><th>Разрешено до</th><th>Состояние / причина ожидания</th></tr></thead><tbody>{trial.trains.map(t=><tr key={t.id}><td>{t.id}</td><td>{t.speed_kmh.toFixed(1)} км/ч</td><td>{(t.distance_m/1000).toFixed(2)} км</td><td>{t.authority_m==null?'Нет допуска':`${(t.authority_m/1000).toFixed(2)} км`}</td><td>{t.state}: {t.reason}</td></tr>)}</tbody></table></div>
 <h4>Проверка сбоя и восстановления</h4><Select value={target} onChange={setTarget} options={trial.trains.map(t=>({value:t.id,label:t.id}))}/><Button disabled={mutateDisabled||!target} onClick={()=>execute('incidents',{run_id:trial.run_id,revision:trial.revision,incident:{kind:'train_breakdown',asset_type:'train',asset_id:target,duration_s:null,note:'Проверка торможения и восстановления'}})}>Поломка поезда</Button><Button disabled={mutateDisabled||!target} onClick={()=>{const t=trial.trains.find(t=>t.id===target);if(t)void execute('incidents',{run_id:trial.run_id,revision:trial.revision,incident:{kind:'track_closure',asset_type:'edge',asset_id:String(t.edge),duration_s:null,note:'Закрытие текущего ребра'}});}}>Закрыть путь под поездом</Button>
 {trial.incidents.filter(i=>i.status!=='cleared').map(i=><p key={i.id}>{i.kind}: {i.asset_id} <Button disabled={mutateDisabled} onClick={()=>execute('clear',{run_id:trial.run_id,revision:trial.revision,incident_id:i.id})}>Восстановить</Button></p>)}
 <details><summary>Журнал решений</summary>{trial.events.map(e=><p key={e.id}>{e.sim_time.toFixed(1)} с · {e.message}</p>)}</details><details><summary>Границы переноса</summary>{trial.limitations.map(x=><p key={x}>{x}</p>)}<p>Светофоры и блоковые границы модельные. Геометрия исходной карты не изменяется.</p></details>
 </>}
 </Modal></section>;
}
