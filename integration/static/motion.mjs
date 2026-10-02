// Interpolate model time, then resolve reservations before locating the marker.
// Never interpolate screen pixels: zoom and camera changes keep the rail binding.
export function poseAt(topology, before, after, train, time) {
  if(time>=after.sim_time_s)return train;
  if(train.execution_frames?.length){
    const frames=train.execution_frames;
    let a=frames[0],b=a;
    for(const frame of frames){if(frame.time_s<=time)a=frame;if(frame.time_s>=time){b=frame;break;}b=frame;}
    const f=Math.max(0,Math.min(1,(time-a.time_s)/(b.time_s-a.time_s||1)));
    const p=a.moving?a:(f===1?b:a);
    const move=p.move;
    if(!p.moving||!move){
      const station=train.route?.[p.leg]||train.station_id;
      return {...train,on_network:p.admitted,station_id:station,station_track_id:p.track,section_id:null,speed_mps:0};
    }
    const section=topology.sections.find(s=>s.id===move.section_id);
    const origin=topology.stations.find(s=>s.id===move.origin),dest=topology.stations.find(s=>s.id===move.destination);
    if(!section||!origin||!dest)return train;
    const end=(b.move?.section_id===move.section_id)?b.x:a.x;
    const x=a.x+(end-a.x)*f;
    return {...train,on_network:p.admitted,station_id:null,station_track_id:null,section_id:section.id,main_track_id:move.main_track_id,
      departure_track_id:p.track,arrival_track_id:p.arrival_track,direction:section.from_station===move.origin?1:-1,
      position_m:origin.position_m+(dest.position_m-origin.position_m)*x/section.length_m,
      speed_mps:a.speed_mps+(b.speed_mps-a.speed_mps)*f};
  }
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
