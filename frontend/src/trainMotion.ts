import type {Snapshot,Topology,Train} from './types';
import {geographicRoute,trackYaw} from './rail3d/geography.ts';

interface Segment {from:number;to:number;started:number;duration:number;direction:number}
const clamp=(n:number,min:number,max:number)=>Math.max(min,Math.min(max,n));

/** Presentation only: bounded interpolation along rail chainage, never speed extrapolation. */
export function createTrainMotion(topology:Topology){
 const route=geographicRoute(topology);
 let latest:Snapshot|null=null,lastReceived=0,lastInstant=true,cadence=500;
 let segments=new Map<string,Segment>();
 const position=(s:Segment,now:number)=>now>=s.started+s.duration?s.to:s.from+(s.to-s.from)*clamp((now-s.started)/s.duration,0,1);
 return {
  accept(snapshot:Snapshot,now:number,instant=false){
   const previous=latest,gap=now-lastReceived;
   const reset=instant||lastInstant||!previous||!snapshot.running||snapshot.clock_held_for_replan||
    snapshot.epoch!==previous.epoch||snapshot.realtime?.stream_id!==previous.realtime?.stream_id||
    snapshot.sim_time_s<previous.sim_time_s||gap>2000;
   const declared=snapshot.realtime?.update_interval_ms;
   if(declared&&Number.isFinite(declared))cadence=clamp(declared,100,1000);
   else if(previous&&snapshot.sim_time_s>previous.sim_time_s&&gap>=100&&gap<=1000)cadence=gap;
   const next=new Map<string,Segment>();
   for(const train of snapshot.trains){
    const old=segments.get(train.id);
    if(!reset&&old&&old.direction===train.direction){
     // Repeated stationary/metadata frames must not restart an arrival already in progress.
     if(old.to===train.position_m){next.set(train.id,old);continue;}
     const from=position(old,now);
     if((train.position_m-from)*train.direction>=0){
      next.set(train.id,{from,to:train.position_m,started:now,duration:cadence,direction:train.direction});continue;
     }
    }
    next.set(train.id,{from:train.position_m,to:train.position_m,started:now,duration:1,direction:train.direction});
   }
   latest=snapshot;segments=next;lastReceived=now;lastInstant=instant;
  },
  sample(now:number):{snapshot:Snapshot|null;moving:boolean}{
   if(!latest)return {snapshot:null,moving:false};
   let moving=false;
   const trains:Train[]=latest.trains.map(train=>{
    const segment=segments.get(train.id)!;
    const chain=position(segment,now);
    if(chain!==segment.to)moving=true;
    const distance=route.distance(chain);
    const yaw=trackYaw(route.point(distance-6.65),route.point(distance+6.65),train.direction);
    return {...train,position_m:chain,coordinate:chain===segment.to?train.coordinate:route.point(distance),
     bearing_deg:((180-yaw*180/Math.PI)%360+360)%360};
   });
   return {snapshot:{...latest,trains},moving};
  },
 };
}
