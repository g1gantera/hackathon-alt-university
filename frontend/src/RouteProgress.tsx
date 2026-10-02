import type {Station,Topology,Train} from './types';

// Stations are placed by real distance, so a long section looks long.
export function RouteProgress({train,topology}:{train:Train;topology:Topology}){
 const route=train.direction>0?topology.stations:[...topology.stations].reverse();
 const share=(position:number)=>train.direction>0?position/topology.length_m:1-position/topology.length_m;
 const progress=Math.min(1,Math.max(0,share(train.position_m)));
 const passed=(station:Station)=>share(station.position_m)<=progress+1e-6;
 const here=topology.stations.find(s=>s.id===train.station_id);
 const next=route.find(s=>!passed(s));
 const text=train.status==='completed'?'Маршрут завершён':here&&train.status!=='moving'?`Стоит: ${here.name}${next?` · далее ${next.name}`:''}`:next?`Следующая: ${next.name}`:'Подходит к конечной';
 return <div className="rf-progress" aria-label={`Пройдено ${Math.round(progress*100)}% маршрута. ${text}`}>
  <div className="rf-progress-track">
   <i style={{width:progress*100+'%'}}/>
   {route.map(s=><span key={s.id} className={passed(s)?'done':''} style={{left:share(s.position_m)*100+'%'}} title={s.name}/>)}
   <b className={train.type} style={{left:progress*100+'%'}}/>
  </div>
  <div className="rf-progress-ends"><span>{route[0].name}</span><span>{route.at(-1)!.name}</span></div>
  <p><strong>{Math.round(progress*100)}%</strong> {text}</p>
 </div>;
}
