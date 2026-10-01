// Interpolate model time, then resolve reservations before locating the marker.
// Never interpolate screen pixels: zoom and camera changes keep the rail binding.
export function poseAt(topology, before, after, train, time) {
  if(time>=after.sim_time_s)return train;
  const old=before.trains.find(t=>t.id===train.id);
  if(!old)return {...train,on_network:false};
  const stops=(after.plan.stops||[]).filter(s=>s.train_id===train.id);
  const stop=stops.find(s=>s.arrival_s<=time&&time<s.departure_s);
  if(stop)return {...train,on_network:true,station_id:stop.station_id,station_track_id:stop.track_id,section_id:null,main_track_id:null,position_m:topology.stations.find(s=>s.id===stop.station_id).position_m,speed_mps:0};
  const move=after.plan.movements.find(m=>m.train_id===train.id&&m.start_s<=time&&time<m.end_s);
  if(!move)return {...train,on_network:false};
  const origin=topology.stations.find(s=>s.id===move.origin),destination=topology.stations.find(s=>s.id===move.destination);
  if(!origin||!destination)return old;
  const sameOld=old.section_id===move.section_id&&before.sim_time_s>=move.start_s;
  const sameNew=train.section_id===move.section_id&&after.sim_time_s<move.end_s;
  const start=sameOld?before.sim_time_s:move.start_s,end=sameNew?after.sim_time_s:move.end_s;
  const a=sameOld?old.position_m:origin.position_m,b=sameNew?train.position_m:destination.position_m;
  const fraction=Math.max(0,Math.min(1,(time-start)/(end-start||1)));
  const section=topology.sections?.find(s=>s.id===move.section_id);
  return {...train,on_network:true,station_id:null,station_track_id:null,section_id:move.section_id,main_track_id:move.main_track_id,
    departure_track_id:stops.find(s=>s.station_id===move.origin)?.track_id,arrival_track_id:stops.find(s=>s.station_id===move.destination)?.track_id,
    direction:section?(section.from_station===move.origin?1:-1):(origin.position_m<destination.position_m?1:-1),position_m:a+(b-a)*fraction};
}
export function frameTime(before,after,received,now,duration,historical=false){
 if(historical||before.epoch!==after.epoch||before.active_plan_id!==after.active_plan_id||after.sim_time_s<=before.sim_time_s)return after.sim_time_s;
 return before.sim_time_s+(after.sim_time_s-before.sim_time_s)*Math.max(0,Math.min(1,(now-received)/Math.max(1,duration)));
}
