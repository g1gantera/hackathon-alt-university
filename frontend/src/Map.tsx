import {useEffect,useRef,useState} from 'react';
import maplibregl, {type GeoJSONSourceSpecification} from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {Topology,Snapshot} from './types';
import {useDispatch} from './store';
import {committedSnapshot,paintIdentifier} from './performanceProbe';

const trainStatus:Record<string,string>={waiting:'На станции',moving:'В движении',completed:'Прибыл'};
let networkRequest:Promise<Extract<GeoJSONSourceSpecification['data'],{type:'FeatureCollection'}>>|null=null;
function loadNetwork(){
 if(!networkRequest)networkRequest=fetch('/api/network').then(r=>{if(!r.ok)throw Error('Network unavailable');return r.json();}).catch(error=>{networkRequest=null;throw error;});
 return networkRequest;
}

export function RailMap({topology,snapshot,allCountry}:{topology:Topology;snapshot:Snapshot;allCountry:boolean}){
 const container=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null);
 const markers=useRef(new Map<string,maplibregl.Marker>());
 const retryNetwork=useRef<()=>void>(()=>{});
 const {selected,select}=useDispatch();
 const [ready,setReady]=useState(false),[networkError,setNetworkError]=useState(false),[networkState,setNetworkState]=useState('Загрузка сети Казахстана…');
 useEffect(()=>{
  if(!container.current)return;
  setReady(false);
  const m=new maplibregl.Map({container:container.current,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'background',type:'background',paint:{'background-color':'#eef1eb'}},{id:'base',source:'base',type:'raster',paint:{'raster-saturation':-.85,'raster-opacity':.53}}]},center:[70.5,52.25],zoom:7.1,attributionControl:{compact:true}});
  map.current=m;
  m.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-right');
  // Rail data does not wait for external raster tiles to finish loading.
  m.once('style.load',()=>{
   m.addSource('corridor',{type:'geojson',data:{type:'FeatureCollection',features:topology.sections.map(s=>({type:'Feature',properties:{id:s.id,status:'open'},geometry:{type:'LineString',coordinates:s.geometry}}))}});
   m.addLayer({id:'corridor-halo',source:'corridor',type:'line',paint:{'line-color':'#ffffff','line-width':7}});
   m.addLayer({id:'corridor',source:'corridor',type:'line',paint:{'line-color':['match',['get','status'],'closed','#dd6652','signal_failure','#d59831','occupied','#167466','#228b79'],'line-width':3}});
   for(const station of topology.stations){
    const element=document.createElement('button');element.type='button';element.className='map-station station-button';element.setAttribute('aria-label',`Станция ${station.name}`);
    const dot=document.createElement('i'),label=document.createElement('span');label.textContent=station.name;element.append(dot,label);
    const details=document.createElement('div');details.className='station-popup';
    const title=document.createElement('strong'),description=document.createElement('p');title.textContent=station.name;description.textContent=`${(station.position_m/1000).toFixed(1)} км · ${station.tracks} путей в демомодели. Станционная вместимость условная.`;details.append(title,description);
    new maplibregl.Marker({element,anchor:'left',offset:[-4,0]}).setLngLat(station.coordinate).setPopup(new maplibregl.Popup({offset:10}).setDOMContent(details)).addTo(m);
   }
   const requestNetwork=()=>{
    setNetworkError(false);setNetworkState('Загрузка сети Казахстана…');
    loadNetwork().then(data=>{
     if(map.current!==m)return;
     if(!m.getSource('network')){
      m.addSource('network',{type:'geojson',data});
      m.addLayer({id:'network',source:'network',type:'line',paint:{'line-color':'#526e7d','line-width':['interpolate',['linear'],['zoom'],4,1,10,2,15,3],'line-opacity':.9}},'corridor-halo');
     }
     setNetworkState(`${data.features.length.toLocaleString('ru')} сегментов · OpenStreetMap`);
    }).catch(()=>{if(map.current===m){setNetworkError(true);setNetworkState('Не удалось загрузить сеть Казахстана');}});
   };
   retryNetwork.current=requestNetwork;requestNetwork();setReady(true);
  });
  const resize=new ResizeObserver(()=>m.resize());resize.observe(container.current);
  return()=>{resize.disconnect();markers.current.clear();m.remove();map.current=null;};
 },[topology]);
 useEffect(()=>{
  if(!ready)return;
  const bounds=new maplibregl.LngLatBounds();
  if(allCountry){bounds.extend([46,40.3]);bounds.extend([88,56]);}
  else topology.sections.forEach(s=>s.geometry.forEach(p=>bounds.extend(p)));
  map.current?.fitBounds(bounds,{padding:55,duration:700});
 },[ready,allCountry,topology]);
 useEffect(()=>{
  const m=map.current;if(!ready||!m)return;
  (m.getSource('corridor') as maplibregl.GeoJSONSource)?.setData({type:'FeatureCollection',features:topology.sections.map(s=>({type:'Feature',properties:{id:s.id,status:snapshot.sections.find(x=>x.id===s.id)?.status||'open'},geometry:{type:'LineString',coordinates:s.geometry}}))});
  for(const [id,marker] of markers.current)if(!snapshot.trains.some(t=>t.id===id)){marker.remove();markers.current.delete(id);}
  snapshot.trains.forEach((train,index)=>{
   let marker=markers.current.get(train.id);
   if(!marker){
    const element=document.createElement('button');element.type='button';element.className='train-pin';element.addEventListener('click',()=>select(train.id));
    const arrow=document.createElement('span');arrow.className='train-bearing';arrow.textContent='↑';
    const label=document.createElement('span');label.className='train-label';element.append(arrow,label);
    marker=new maplibregl.Marker({element,anchor:'center',offset:[0,0]}).setLngLat(train.coordinate).addTo(m);markers.current.set(train.id,marker);
   }
   marker.setLngLat(train.coordinate);
   const element=marker.getElement();element.classList.toggle('freight',train.type==='freight');element.classList.toggle('selected',selected===train.id);element.classList.toggle('stopped',train.status!=='moving');
   let label=element.querySelector('.train-label') as HTMLElement;
   const paintId=paintIdentifier(snapshot,'map',train.id);
   if(paintId&&label.getAttribute('elementtiming')!==paintId){
    const replacement=label.cloneNode(false) as HTMLElement;
    replacement.setAttribute('elementtiming',paintId);label.replaceWith(replacement);label=replacement;
   }
   label.textContent=`${train.number} · ${trainStatus[train.status]||train.status}`;
   label.style.top=`${train.status==='moving'?-12:(Math.floor(index/2)-1)*25}px`;
   (element.querySelector('.train-bearing') as HTMLElement).style.transform=`rotate(${train.bearing_deg-m.getBearing()}deg)`;
   element.title=`№ ${train.number} · ${train.route_name} · ${trainStatus[train.status]} · ${(train.speed_mps*3.6).toFixed(0)} км/ч`;
   element.setAttribute('aria-label',element.title);
  });
  committedSnapshot(snapshot,'map');
 },[snapshot,ready,selected,topology,select]);
 const selectedTrain=snapshot.trains.find(t=>t.id===selected);
 return <div className="map-wrapper"><div ref={container} className="map-canvas"/><div className="map-caption"><span className="dot green"/>{networkState}{networkError&&<button onClick={()=>retryNetwork.current()}>Повторить</button>}</div>{selectedTrain&&<div className="map-selected"><strong>№ {selectedTrain.number} · {trainStatus[selectedTrain.status]}</strong><span>{selectedTrain.route_name}</span><small>{(selectedTrain.speed_mps*3.6).toFixed(0)} км/ч · движение моделируется</small></div>}<div className="map-distance"><strong>{(topology.length_m/1000).toFixed(1)}</strong><span>км маршрута</span></div></div>;
}

export function TrackDiagram({topology,snapshot}:{topology:Topology;snapshot:Snapshot}){
 const {selected,select}=useDispatch();
 const x=(p:number)=>75+p/topology.length_m*1050;
 return <div className="schematic"><svg viewBox="0 0 1200 350" aria-label="Схема станций и перегонов">
 {topology.sections.map((section,i)=>{const state=snapshot.sections.find(s=>s.id===section.id);return <g key={section.id}><line x1={x(topology.stations[i].position_m)} y1="170" x2={x(topology.stations[i+1].position_m)} y2="170" stroke={state?.status==='closed'?'#de6856':state?.status==='occupied'?'#148976':'#adc3c4'} strokeWidth="7"/><circle cx={(x(topology.stations[i].position_m)+x(topology.stations[i+1].position_m))/2} cy="205" r="5" fill={state?.signal==='green'?'#148976':'#de6856'}/><text x={(x(topology.stations[i].position_m)+x(topology.stations[i+1].position_m))/2} y="232" textAnchor="middle" fontSize="12">{(section.length_m/1000).toFixed(1)} км</text></g>;})}
 {topology.stations.map(s=><g key={s.id}><path d={`M${x(s.position_m)-25} 170 l12 -24 h26 l12 24 M${x(s.position_m)-25} 170 l12 24 h26 l12 -24`} fill="none" stroke="#52767a" strokeWidth="3"/><circle cx={x(s.position_m)} cy="170" r="6" fill="white" stroke="#31565e" strokeWidth="3"/><text x={x(s.position_m)} y="285" textAnchor="middle" fontSize="15">{s.name}</text><text x={x(s.position_m)} y="307" textAnchor="middle" fontSize="11" fill="#7c9093">{s.tracks} пути · стрелка {snapshot.switches.find(w=>w.station_id===s.id)?.available?'свободна':'занята'}</text></g>)}
 {snapshot.trains.map((t,i)=><g key={t.id} role="button" tabIndex={0} aria-label={`Поезд ${t.number}`} onClick={()=>select(t.id)} onKeyDown={e=>{if(e.key==='Enter')select(t.id)}} style={{cursor:'pointer'}}><rect x={x(t.position_m)-23} y={40+Math.floor(i/2)*25} width="46" height="21" rx="5" fill={t.type==='passenger'?'#137f70':'#d09939'} stroke={selected===t.id?'#122f39':'none'} strokeWidth="3"/><text x={x(t.position_m)} y={55+Math.floor(i/2)*25} textAnchor="middle" fontSize="12" fill="white">{t.direction>0?'›':'‹'}{t.number}</text></g>)}
 </svg><p>Однопутная модель · два пути на промежуточных станциях · сигналы защищают перегоны</p></div>;
}
