import {useEffect,useRef,useState} from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {Topology,Snapshot,Train} from './types';
import {useDispatch} from './store';

function coordinate(train:Train,topology:Topology):[number,number]{
 const position=Math.max(0,Math.min(topology.length_m,train.position_m));
 const index=Math.max(0,topology.stations.findIndex(s=>s.position_m>=position)-1);
 const section=topology.sections[index];
 const fraction=Math.min(1,Math.max(0,(position-topology.stations[index].position_m)/section.length_m));
 const points=section.geometry;
 const lengths=[0];
 for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i];const dx=(b[0]-a[0])*Math.cos((a[1]+b[1])*Math.PI/360);lengths.push(lengths[i-1]+Math.hypot(dx,b[1]-a[1]));}
 const target=fraction*lengths.at(-1)!;
 let i=lengths.findIndex(x=>x>=target);if(i<=0)return points[0];
 const ratio=(target-lengths[i-1])/(lengths[i]-lengths[i-1]||1);
 return [points[i-1][0]+ratio*(points[i][0]-points[i-1][0]),points[i-1][1]+ratio*(points[i][1]-points[i-1][1])];
}

export function RailMap({topology,snapshot,allCountry}:{topology:Topology;snapshot:Snapshot;allCountry:boolean}){
 const container=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null);
 const markers=useRef(new Map<string,maplibregl.Marker>());
 const {selected,select}=useDispatch();
 const [ready,setReady]=useState(false),[networkState,setNetworkState]=useState('Загрузка сети Казахстана…');
 useEffect(()=>{
  if(!container.current)return;
  const m=new maplibregl.Map({container:container.current,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'background',type:'background',paint:{'background-color':'#eef1eb'}},{id:'base',source:'base',type:'raster',paint:{'raster-saturation':-.85,'raster-opacity':.53}}]},center:[70.5,52.25],zoom:7.1,attributionControl:{compact:true}});
  map.current=m;
  m.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-right');
  m.on('load',()=>{
   m.addSource('corridor',{type:'geojson',data:{type:'FeatureCollection',features:topology.sections.map(s=>({type:'Feature',properties:{id:s.id,status:'open'},geometry:{type:'LineString',coordinates:s.geometry}}))}});
   m.addLayer({id:'corridor-halo',source:'corridor',type:'line',paint:{'line-color':'#ffffff','line-width':8}});
   m.addLayer({id:'corridor',source:'corridor',type:'line',paint:{'line-color':['match',['get','status'],'closed','#dd6652','signal_failure','#d59831','occupied','#167466','#228b79'],'line-width':4}});
   for(const station of topology.stations){const element=document.createElement('div');element.className='map-station';const dot=document.createElement('i');const label=document.createElement('span');label.textContent=station.name;element.append(dot,label);new maplibregl.Marker({element,anchor:'left'}).setLngLat(station.coordinate).addTo(m);}
   fetch('/api/network').then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{if(map.current!==m)return;m.addSource('network',{type:'geojson',data});m.addLayer({id:'network',source:'network',type:'line',paint:{'line-color':'#879ba0','line-width':1,'line-opacity':.55}},'corridor-halo');setNetworkState(`${data.features.length.toLocaleString('ru')} сегментов · OpenStreetMap`);}).catch(()=>setNetworkState('Сеть не загрузилась — доступен маршрут демо'));
   setReady(true);
  });
  const resize=new ResizeObserver(()=>m.resize());resize.observe(container.current);
  return()=>{resize.disconnect();markers.current.clear();m.remove();map.current=null;};
 },[topology]);
 useEffect(()=>{if(ready)map.current?.fitBounds(allCountry?[[46,40.3],[88,56]]:[[69.1,51.05],[71.8,53.5]],{padding:55,duration:700});},[ready,allCountry]);
 useEffect(()=>{
  const m=map.current;if(!ready||!m)return;
  (m.getSource('corridor') as maplibregl.GeoJSONSource)?.setData({type:'FeatureCollection',features:topology.sections.map(s=>({type:'Feature',properties:{id:s.id,status:snapshot.sections.find(x=>x.id===s.id)?.status||'open'},geometry:{type:'LineString',coordinates:s.geometry}}))});
  snapshot.trains.forEach((train,index)=>{
   let marker=markers.current.get(train.id);
   if(!marker){const element=document.createElement('button');element.addEventListener('click',()=>select(train.id));element.setAttribute('aria-label',`Поезд ${train.number}`);marker=new maplibregl.Marker({element,anchor:'right',offset:[-10,index%4*22-25]}).setLngLat(coordinate(train,topology)).addTo(m);markers.current.set(train.id,marker);}
   marker.setOffset([-8,train.status==='waiting'||train.status==='completed'?Math.floor(index/2)*23-35:0]);marker.setLngLat(coordinate(train,topology));const element=marker.getElement();element.className=`maplibregl-marker maplibregl-marker-anchor-right map-train ${train.type} ${selected===train.id?'selected':''}`;element.textContent=`${train.direction>0?'↑':'↓'} ${train.number}`;element.title=`${(train.speed_mps*3.6).toFixed(0)} км/ч · +${(train.delay_s/60).toFixed(1)} мин`;
  });
 },[snapshot,ready,selected,topology,select]);
 return <div className="map-wrapper"><div ref={container} className="map-canvas"/><div className="map-caption"><span className="dot green"/>{networkState}</div><div className="map-distance"><strong>{(topology.length_m/1000).toFixed(1)}</strong><span>км маршрута</span></div></div>;
}

export function TrackDiagram({topology,snapshot}:{topology:Topology;snapshot:Snapshot}){
 const {selected,select}=useDispatch();
 const width=Math.max(1200,topology.stations.length*145);
 const x=(p:number)=>75+p/topology.length_m*(width-150);
 return <div className="schematic"><svg style={{minWidth:width}} viewBox={`0 0 ${width} 350`} aria-label="Схема станций и перегонов">
 {topology.sections.map((section,i)=>{const state=snapshot.sections.find(s=>s.id===section.id);return <g key={section.id}>{(section.main_tracks||[{id:'1'}]).map((track,j)=><line key={track.id} x1={x(topology.stations[i].position_m)} y1={170+j*10} x2={x(topology.stations[i+1].position_m)} y2={170+j*10} stroke={['closed','signal_failure'].includes(state?.tracks?.find(t=>t.id===track.id)?.status||state?.status||'')?'#de6856':(state?.tracks?.find(t=>t.id===track.id)?.status||state?.status)==='occupied'?'#148976':'#adc3c4'} strokeWidth="4"/>)}<circle cx={(x(topology.stations[i].position_m)+x(topology.stations[i+1].position_m))/2} cy="205" r="5" fill={state?.signal==='green'?'#148976':'#de6856'}/><text x={(x(topology.stations[i].position_m)+x(topology.stations[i+1].position_m))/2} y="232" textAnchor="middle" fontSize="12">{(section.length_m/1000).toFixed(1)} км</text></g>;})}
 {topology.stations.map(s=><g key={s.id}><path d={`M${x(s.position_m)-25} 170 l12 -24 h26 l12 24 M${x(s.position_m)-25} 170 l12 24 h26 l12 -24`} fill="none" stroke="#52767a" strokeWidth="3"/><circle cx={x(s.position_m)} cy="170" r="6" fill="white" stroke="#31565e" strokeWidth="3"/><text x={x(s.position_m)} y="285" textAnchor="middle" fontSize="15">{s.name}</text><text x={x(s.position_m)} y="307" textAnchor="middle" fontSize="11" fill="#7c9093">{s.tracks} пути · стрелка {snapshot.switches.find(w=>w.station_id===s.id)?(snapshot.switches.find(w=>w.station_id===s.id)?.status==='blocked'?'запрет':snapshot.switches.find(w=>w.station_id===s.id)?.available?'свободна':'маршрут замкнут'):'н/д'}</text><text x={x(s.position_m)} y="327" textAnchor="middle" fontSize="11" fill="#52767a">{snapshot.switches.find(w=>w.station_id===s.id)?.position==='reverse'?'Отклонённое положение':'Прямое положение'}</text></g>)}
 {snapshot.trains.map((t,i)=><g key={t.id} role="button" tabIndex={0} aria-label={`Поезд ${t.number}`} onClick={()=>select(t.id)} onKeyDown={e=>{if(e.key==='Enter')select(t.id)}} style={{cursor:'pointer'}}><rect x={x(t.position_m)-23} y={40+Math.floor(i/2)*25} width="46" height="21" rx="5" fill={t.type==='passenger'?'#137f70':'#d09939'} stroke={selected===t.id?'#122f39':'none'} strokeWidth="3"/><text x={x(t.position_m)} y={55+Math.floor(i/2)*25} textAnchor="middle" fontSize="12" fill="white">{t.direction>0?'›':'‹'}{t.number}</text></g>)}
 </svg><p>{topology.engine==='logic'?'Многопутная модель · одна условная горловина на станцию, не реальные номера стрелок':'Однопутная модель · сигналы защищают перегоны'}</p></div>;
}
