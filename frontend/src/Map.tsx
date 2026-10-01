import {useEffect,useMemo,useRef,useState} from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {Topology,Snapshot,Train} from './types';
import {useDispatch} from './store';
import {createLocator,trackIndex,connectorFeatures} from '../../integration/static/coordinates.mjs';
import {trackCategories,trackCategory} from '../../integration/static/track-colors.mjs';
import {trainNotice,incidentNotices} from '../../integration/static/map-alerts.mjs';
import './tracks.css';
import {signalMarkup} from './signals';
import {useMotion} from './useMotion';
type FeatureCollection={type:'FeatureCollection';features:{type:'Feature';properties:Record<string,unknown>|null;geometry:{type:'LineString';coordinates:number[][]}}[]};

const time=(seconds:number)=>new Date((8*3600+seconds)*1000).toISOString().slice(11,19);
const trackName=(t:Train)=>t.station_track_id?`станционный ${t.station_track_id}`:t.main_track_id?`главный ${t.main_track_id}`:'ожидает допуска';
const trainTitle=(t:Train)=>`${t.number} · ${trackName(t)} · ${(t.speed_mps*3.6).toFixed(0)} км/ч${t.wait_reason?` · ${t.wait_reason}`:''}${t.next_departure_s!=null?` · отправление ${time(t.next_departure_s)}`:''}`;

function Queue({snapshot}:{snapshot:Snapshot}){
 const {select}=useDispatch();
 const pending=snapshot.trains.filter(t=>t.on_network===false&&t.status!=='completed');
 return pending.length?<div className="track-queue"><strong>До входа на участок:</strong>{pending.map(t=><button key={t.id} onClick={()=>select(t.id)} title={t.wait_reason||''}>{t.number} · {t.status==='scheduled'?'по расписанию':'очередь'}{t.admission_s!=null?` до ${time(t.admission_s)}`:''}</button>)}</div>:null;
}

function trackFeatures(topology:Topology,snapshot?:Snapshot):FeatureCollection{
 return {type:'FeatureCollection',features:topology.sections.flatMap(s=>(s.main_tracks||[{id:'1',direction:'both'}]).map(t=>{
  const state=snapshot?.sections.find(x=>x.id===s.id);
  return {type:'Feature' as const,properties:{section:s.id,track:t.id,wear:snapshot?.track_wear?.[`main_track:${s.id}:${t.id}`]?.wear_pct??-1,status:state?.tracks?.find(x=>x.id===t.id)?.status||state?.status||'open'},geometry:{type:'LineString' as const,coordinates:t.geometry||s.geometry}};
 }))};
}

export function RailMap({topology,snapshot,allCountry,historical=false}:{topology:Topology;snapshot:Snapshot;allCountry:boolean;historical?:boolean}){
 const container=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null);
 const movingTrains=useMotion(topology,snapshot,historical);
 const controls=useRef(new Map<string,maplibregl.Marker>());
 const markers=useRef(new Map<string,maplibregl.Marker>()),alarms=useRef(new Map<string,maplibregl.Marker>());
 const {selected,select}=useDispatch();
 const locate=useMemo(()=>createLocator(topology),[topology]);
 const [ready,setReady]=useState(false),[networkState,setNetworkState]=useState('Загрузка сети Казахстана…');
 const [visible,setVisible]=useState(()=>new Set(trackCategories.map(([id])=>id)));
 useEffect(()=>{
  if(!container.current)return;
  setReady(false);
  const m=new maplibregl.Map({container:container.current,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'background',type:'background',paint:{'background-color':'#eef1eb'}},{id:'base',source:'base',type:'raster',paint:{'raster-saturation':-.85,'raster-opacity':.4}}]},center:[70.5,52.25],zoom:7.1,attributionControl:{compact:true}});
  map.current=m;
  m.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-right');
  m.on('load',()=>{
   m.addSource('corridor',{type:'geojson',data:trackFeatures(topology)});
   m.addLayer({id:'corridor',source:'corridor',type:'line',paint:{'line-color':'#253047','line-width':2,'line-opacity':.7,'line-dasharray':[4,3]}});
   m.addSource('turnouts',{type:'geojson',data:connectorFeatures(topology)});
   m.addLayer({id:'turnouts',source:'turnouts',type:'line',paint:{'line-color':'#9260ad','line-width':3}});
   m.addLayer({id:'wear',source:'corridor',type:'line',filter:['>=',['get','wear'],0],paint:{'line-color':['step',['get','wear'],'#4a9b73',40,'#d8a528',65,'#e77d2d',85,'#c6403a'],'line-width':6,'line-opacity':.55}});
   m.addLayer({id:'restrictions',source:'corridor',type:'line',filter:['in',['get','status'],['literal',['closed','signal_failure']]],paint:{'line-color':'#e55449','line-width':4,'line-dasharray':[2,2]}});
   for(const station of topology.stations){const element=document.createElement('div');element.className='map-station';const dot=document.createElement('i');const label=document.createElement('span');label.textContent=station.name;element.append(dot,label);element.title=`${station.tracks} пути в модели · ${station.mapped_tracks??'—'} линий в сечении OSM`;new maplibregl.Marker({element,anchor:'left'}).setLngLat(station.coordinate).addTo(m);}
   fetch('/api/network').then(r=>{if(!r.ok)throw Error();return r.json();}).then((data:FeatureCollection)=>{
    if(map.current!==m)return;
    data.features.forEach(f=>{f.properties={...f.properties,category:trackCategory(f.properties||{})};});
    m.addSource('network',{type:'geojson',data});
    for(const [id,,color] of trackCategories)m.addLayer({id:`network-${id}`,source:'network',type:'line',filter:['==',['get','category'],id],paint:{'line-color':color,'line-width':['interpolate',['linear'],['zoom'],6,1,15,2,18,3],'line-opacity':.9,...(id==='crossover'?{'line-dasharray':[3,2]}:{})}},'corridor');
    setNetworkState(`${data.features.length.toLocaleString('ru')} сегментов · OpenStreetMap`);
   }).catch(()=>setNetworkState('Сеть не загрузилась — доступен маршрут демо'));
   setReady(true);
  });
  const resize=new ResizeObserver(()=>m.resize());resize.observe(container.current);
  return()=>{resize.disconnect();markers.current.clear();alarms.current.clear();controls.current.clear();m.remove();map.current=null;};
 },[topology]);
 useEffect(()=>{if(ready)map.current?.fitBounds(allCountry?[[46,40.3],[88,56]]:[[Math.min(...topology.stations.map(s=>s.coordinate[0])),Math.min(...topology.stations.map(s=>s.coordinate[1]))],[Math.max(...topology.stations.map(s=>s.coordinate[0])),Math.max(...topology.stations.map(s=>s.coordinate[1]))]],{padding:55,duration:700});},[ready,allCountry,topology]);
 useEffect(()=>{
  const m=map.current;if(!m||!ready)return;
  for(const [id] of trackCategories)if(m.getLayer(`network-${id}`))m.setLayoutProperty(`network-${id}`,'visibility',visible.has(id)?'visible':'none');
 },[ready,visible,networkState]);
 useEffect(()=>{
  const m=map.current;if(!ready||!m)return;
  (m.getSource('corridor') as maplibregl.GeoJSONSource)?.setData(trackFeatures(topology,snapshot));
  const controlIds=new Set<string>();
  const putControl=(id:string,position:[number,number],label:string,title:string,css:string)=>{
   controlIds.add(id);let marker=controls.current.get(id);
   if(!marker){const el=document.createElement('div');marker=new maplibregl.Marker({element:el,anchor:'bottom'}).setLngLat(position).addTo(m);controls.current.set(id,marker);}
   marker.setLngLat(position);const el=marker.getElement();el.className='maplibregl-marker traffic-control '+css;el.textContent=label;el.title=title;
  };
  for(const signal of snapshot.signals||[]){
   if(signal.type==='block'&&m.getZoom()<11)continue;
   const section=topology.sections.find(s=>s.id===signal.section_id);if(!section)continue;
   const from=signal.station_id===section.from_station;
   const station=topology.stations.find(s=>s.id===signal.station_id)!;
   const other=topology.stations.find(s=>s.id===(from?section.to_station:section.from_station))!;
   const position=locate({on_network:true,position_m:station.position_m+(other.position_m-station.position_m)*(signal.offset_m||250)/section.length_m,section_id:section.id,main_track_id:signal.main_track_id} as Train);
   if(position){putControl(signal.id,position,'',`${signal.id} · ${signal.reason}${signal.train_id?' · '+signal.train_id:''} · модельный сигнал`,'signal-head');controls.current.get(signal.id)!.setOffset([signal.type==='exit'?-16:16,0]);controls.current.get(signal.id)!.getElement().innerHTML=signalMarkup(signal);}
  }
  for(const sw of snapshot.switches){const station=topology.stations.find(s=>s.id===sw.station_id);if(station)putControl('post:'+sw.station_id,station.coordinate,`${sw.position==='reverse'?'⑂':'⑃'} ${sw.available?'свободна':'замкнута'}`,`Модельный пост ${station.name} · стрелка ${sw.position==='reverse'?'боковой путь':'прямо'}${sw.train_id?' · маршрут '+sw.train_id:''}. Управление через назначение пути и проверенный план.`, 'post');}
  for(const [id,marker] of controls.current)if(!controlIds.has(id)){marker.remove();controls.current.delete(id);}
  const present=new Set<string>();
  for(const train of snapshot.trains){
   const position=locate(train);if(!position)continue;
   present.add(train.id);
   let marker=markers.current.get(train.id);
   if(!marker){
    const element=document.createElement('button');element.type='button';
    const stem=document.createElementNS('http://www.w3.org/2000/svg','svg');stem.classList.add('train-stem');const line=document.createElementNS('http://www.w3.org/2000/svg','line');line.setAttribute('x1','0');line.setAttribute('y1','0');stem.append(line);const label=document.createElement('span');element.append(stem,label);
    element.addEventListener('click',()=>select(train.id));
    marker=new maplibregl.Marker({element,anchor:'center'}).setLngLat(position).addTo(m);markers.current.set(train.id,marker);
   }
   const element=marker.getElement(),lane=trackIndex(train,topology);
   const notice=trainNotice(train,snapshot.plan.applicable!==false&&!snapshot.awaiting_plan);
   element.className=`maplibregl-marker track-train ${train.type} lane-${lane%2} ${notice.level} ${selected===train.id?'selected':''}`;
   const stem=element.querySelector('line')!;stem.setAttribute('x2',lane%2?'20':'-20');stem.setAttribute('y2',lane%2?'16':'-16');
   element.setAttribute('aria-label',trainTitle(train));element.title=trainTitle(train)+(notice.text?` · ${notice.text}`:'');
   element.querySelector('span')!.textContent=`${train.direction>0?'›':'‹'} ${train.number} · ${train.station_track_id||train.main_track_id||''}${notice.text?' · ⚠ '+notice.text:''}`;
  }
  for(const [id,marker] of markers.current)if(!present.has(id)){marker.remove();markers.current.delete(id);}
  const active=new Set<string>();
  for(const alert of incidentNotices(snapshot,topology)){
   if(!alert.coordinate)continue;active.add(alert.id);let marker=alarms.current.get(alert.id);
   if(!marker){const el=document.createElement('div');el.className='map-incident';marker=new maplibregl.Marker({element:el,anchor:'bottom'}).setLngLat(alert.coordinate).addTo(m);alarms.current.set(alert.id,marker);}
   marker.setLngLat(alert.coordinate);marker.getElement().textContent=`⚠ ${alert.text}`;marker.getElement().title=`${alert.target} · до ${time(alert.end_s)}`;
  }
  for(const [id,marker] of alarms.current)if(!active.has(id)){marker.remove();alarms.current.delete(id);}


 },[snapshot,ready,selected,topology,select,locate]);
 useEffect(()=>{for(const train of movingTrains){const marker=markers.current.get(train.id);if(!marker)continue;const point=locate(train);marker.getElement().style.visibility=point?'visible':'hidden';if(point)marker.setLngLat(point);}},[movingTrains,locate,ready]);
 return <><div className="map-wrapper"><div ref={container} className="map-canvas"/>
  <details className="track-legend" open><summary>Типы путей</summary>{trackCategories.map(([id,label,color])=><label key={id}><input type="checkbox" checked={visible.has(id)} onChange={()=>setVisible(old=>{const next=new Set(old);if(next.has(id))next.delete(id);else next.add(id);return next;})}/><i style={{borderColor:color,borderTopStyle:id==='crossover'?'dashed':'solid'}}/>{label}</label>)}<p>Пунктир — пути модели.<br/>Сплошные — геометрия OSM.<br/>Фиолетовый — модельные съезды.<br/>● Светофор: разрешение только указанному поезду.<br/>⑂ Пост и положение стрелки (модель).</p><p>Учебный износ: 🟢 &lt;40 · 🟡 40–64 · 🟠 65–84 · 🔴 ≥85%. Без оценки — без подсветки.</p></details>
  <div className="map-caption"><span className="dot green"/>{networkState}</div><div className="map-distance"><strong>{(topology.length_m/1000).toFixed(1)}</strong><span>км маршрута</span></div></div>
  <MapWarnings topology={topology} snapshot={snapshot}/><Queue snapshot={snapshot}/><p className="track-note">{topology.track_geometry_note||'Назначенные пути — из активного плана.'}</p></>;
}

export function TrackDiagram({topology,snapshot,historical=false}:{topology:Topology;snapshot:Snapshot;historical?:boolean}){
 const {selected,select}=useDispatch();
 const movingTrains=useMotion(topology,snapshot,historical);
 const scroll=useRef<HTMLDivElement>(null);
 const maxTracks=Math.max(...topology.stations.map(s=>s.tracks));
 const height=Math.max(340,115+maxTracks*80+130);
 const width=topology.stations.length*210, stationX=(i:number)=>100+i*210, laneY=(i:number)=>115+i*80;
 const x=(position:number)=>{let i=topology.stations.findIndex(s=>s.position_m>=position);if(i<=0)return i===0?stationX(0):stationX(topology.stations.length-1);const a=topology.stations[i-1],b=topology.stations[i];return stationX(i-1)+(position-a.position_m)/(b.position_m-a.position_m)*210;};
 const trainY=(t:Train)=>{
  if(t.station_id)return laneY(trackIndex(t,topology));
  const section=topology.sections.find(s=>s.id===t.section_id);if(!section)return laneY(0);
  const a=topology.stations.find(s=>s.id===section.from_station)!,b=topology.stations.find(s=>s.id===section.to_station)!;
  const f=Math.max(0,Math.min(1,(t.position_m-a.position_m)/(b.position_m-a.position_m)));
  const y=laneY(trackIndex(t,topology)),forward=t.direction!==-1;
  const from=a.track_layout?.findIndex(p=>p.id===(forward?t.departure_track_id:t.arrival_track_id))??0;
  const to=b.track_layout?.findIndex(p=>p.id===(forward?t.arrival_track_id:t.departure_track_id))??0;
  return f<.22?laneY(Math.max(0,from))+(y-laneY(Math.max(0,from)))*f/.22:f>.78?y+(laneY(Math.max(0,to))-y)*(f-.78)/.22:y;
 };
 if(topology.network){const locate=createLocator(topology),minLon=Math.min(...topology.stations.map(s=>s.coordinate[0])),maxLat=Math.max(...topology.stations.map(s=>s.coordinate[1]));const xy=(p:number[])=>[(p[0]-minLon)*180+40,(maxLat-p[1])*290+40];return <div className="schematic" style={{maxHeight:650,overflow:'auto'}}><svg viewBox="0 0 1900 1400" style={{minWidth:1500}} aria-label="Общая схема сети">{topology.sections.map(s=><polyline key={s.id} points={s.geometry.map(p=>xy(p).join(',')).join(' ')} fill="none" stroke="#708b94" strokeWidth={2}/>)}{topology.stations.map(s=>{const [x,y]=xy(s.coordinate);return <g key={s.id}><circle cx={x} cy={y} r={3}/><text x={x+5} y={y-5} fontSize={10}>{s.name}</text></g>})}{movingTrains.filter(t=>t.on_network!==false).map(t=>{const p=locate(t);if(!p)return null;const [x,y]=xy(p);return <g key={t.id} onClick={()=>select(t.id)} style={{cursor:'pointer'}}><circle cx={x} cy={y} r={6} fill={t.type==='passenger'?'#137f70':'#bd8524'}/><text x={x+8} y={y+10} fontSize={11}>{t.number}</text></g>})}</svg></div>;}
 return <><div className="scheme-scroll"><button onClick={()=>scroll.current?.scrollBy({left:-500,behavior:'smooth'})}>← По схеме</button><span>Прокрутка внизу схемы · Shift + колесо</span><button onClick={()=>scroll.current?.scrollBy({left:500,behavior:'smooth'})}>По схеме →</button></div><div className="schematic" ref={scroll} tabIndex={0}><svg style={{minWidth:width}} viewBox={`0 0 ${width} ${height}`} aria-label="Пути и фактические назначения поездов">
 {topology.sections.map((section,i)=>{const state=snapshot.sections.find(s=>s.id===section.id);return <g key={section.id}>
  {(section.main_tracks||[{id:'1',direction:'both'}]).map((track,j)=>{const status=state?.tracks?.find(t=>t.id===track.id)?.status||state?.status;return <g key={track.id}>{Array.from({length:Math.max(topology.stations[i].tracks,topology.stations[i+1].tracks)},(_,lane)=>lane).map(lane=><g key={lane}>{lane<topology.stations[i].tracks&&<line x1={stationX(i)} y1={laneY(lane)} x2={stationX(i)+46.2} y2={laneY(j)} stroke="#9260ad" strokeWidth="1"/>}{lane<topology.stations[i+1].tracks&&<line x1={stationX(i+1)-46.2} y1={laneY(j)} x2={stationX(i+1)} y2={laneY(lane)} stroke="#9260ad" strokeWidth="1"/>}</g>)}<line x1={stationX(i)+45} y1={laneY(j)} x2={stationX(i+1)-45} y2={laneY(j)} stroke={['closed','signal_failure'].includes(status||'')?'#e55449':'#253047'} strokeWidth={status==='occupied'?5:3}/>{(snapshot.signals||[]).filter(sig=>sig.section_id===section.id&&sig.main_track_id===track.id&&sig.type!=='warning').map(sig=><g key={sig.id} transform={`translate(${sig.station_id===section.from_station?stationX(i)+50:stationX(i+1)-50},${laneY(j)-(sig.type==='entry'?52:18)})`}><title>{sig.type} · {sig.reason} {sig.train_id}</title><rect x={-5} y={-21} width={10} height={26} rx={4} fill="#20282c"/>{['yellow','green','red'].map((color,k)=><circle key={color} cy={-16+k*8} r={3} fill={sig.aspect===color?{yellow:'#ffd84d',green:'#27e37c',red:'#ff4d4d'}[color]:'#455053'}/>)}</g>)}<text x={stationX(i)+105} y={laneY(j)+22} textAnchor="middle" fontSize="11">{track.direction==='b_to_a'?'←':'→'} путь {track.id}{snapshot.track_wear?.[`main_track:${section.id}:${track.id}`]?` · ${snapshot.track_wear[`main_track:${section.id}:${track.id}`].wear_pct}%`:" · н/д"}</text></g>;})}
  <text x={stationX(i)+105} y={height-85} textAnchor="middle" fontSize="12">{(section.length_m/1000).toFixed(1)} км</text></g>;})}
 {topology.stations.map((s,i)=><g key={s.id}>
  <title>{s.name}</title><text x={stationX(i)} y="42" textAnchor="middle" fontSize="13">{s.name.length>25?s.name.slice(0,23)+'…':s.name}</text>
  {(s.track_layout||Array.from({length:s.tracks},(_,j)=>({id:String(j+1)}))).map((t,j)=><g key={t.id}><line x1={stationX(i)-45} y1={laneY(j)} x2={stationX(i)+45} y2={laneY(j)} stroke={snapshot.stations?.find(st=>st.id===s.id)?.tracks.find(tr=>tr.id===t.id)?.status==='closed'?'#e55449':'#19a58d'} strokeWidth="5"/><text x={stationX(i)} y={laneY(j)+22} textAnchor="middle" fontSize="11">{t.id}{snapshot.track_wear?.[`track:${s.id}:${t.id}`]?` · ${snapshot.track_wear[`track:${s.id}:${t.id}`].wear_pct}%`:" · н/д"}</text></g>)}
  <text x={stationX(i)} y={height-50} textAnchor="middle" fontSize="11">{s.tracks} пути модели · {s.mapped_tracks??'—'} в сечении OSM</text>
  <text x={stationX(i)} y={height-30} textAnchor="middle" fontSize="11">Горловина: {snapshot.switches.find(w=>w.station_id===s.id)?.available?'свободна':'маршрут замкнут / запрет'}</text>
 </g>)}
 {movingTrains.filter(t=>t.on_network!==false).map(t=><g key={t.id} role="button" tabIndex={0} aria-label={trainTitle(t)} onClick={()=>select(t.id)} onKeyDown={e=>{if(e.key==='Enter')select(t.id)}} style={{cursor:'pointer'}}><title>{trainTitle(t)} {trainNotice(t,snapshot.plan.applicable!==false).text}</title><rect x={x(t.position_m)-45} y={trainY(t)-12} width="90" height="24" rx="5" fill={trainNotice(t,snapshot.plan.applicable!==false).level==='danger'?'#c8483c':t.type==='passenger'?'#137f70':'#bd8524'} stroke={selected===t.id?'#122f39':'white'} strokeWidth="2"/><text x={x(t.position_m)} y={trainY(t)+4} textAnchor="middle" fontSize="11" fill="white">{t.direction>0?'›':'‹'} {t.number}</text></g>)}
 </svg></div><MapWarnings topology={topology} snapshot={snapshot}/><Queue snapshot={snapshot}/><p className="track-note">Каждая полоса — отдельный ресурс плана. Обгон и пропуск — на станциях, после освобождения пути и горловины.</p></>;
}

function MapWarnings({snapshot,topology}:{snapshot:Snapshot;topology:Topology}){
 const {select}=useDispatch();
 const trains=snapshot.trains.map(t=>({train:t,...trainNotice(t,snapshot.plan.applicable!==false&&!snapshot.awaiting_plan)})).filter(n=>n.text);
 const incidents=incidentNotices(snapshot,topology);
 const wear=Object.entries(snapshot.track_wear||{}).filter(([,v])=>v.wear_pct>=40);
 return <div className="map-warnings" aria-label="Предупреждения на карте">{trains.map(n=><button key={n.train.id} className={n.level} onClick={()=>select(n.train.id)}>⚠ {n.train.number}: {n.text}</button>)}{incidents.map(i=><span className="danger" key={i.id}>⚠ {i.text} · {i.target} · до {time(i.end_s)}</span>)}{wear.map(([id,v])=><span className={v.wear_pct>=85?'danger':'warning'} key={id}>Учебный износ {v.wear_pct}% · {id}</span>)}{!trains.length&&!incidents.length&&!wear.length&&<span>Нет предупреждений на текущий момент</span>}</div>;
}
