import type {Snapshot,Topology,Train} from '../types';
import {geographicRoute,mercator,trackYaw,type Coordinate} from './geography.ts';

export const TRACK_SPACING=7,YARD_HALF=1600,YARD_TAPER=300;
export interface TrackAssignment {stationId:string;index:number}
export type DisplayTrain=Train&{displayTrack?:TrackAssignment};
export interface Yard {id:string;center:number;from:number;to:number;tracks:number}

/** Illustrative station sidings; they do not change dispatcher resources or physical telemetry. */
export function createStationTracks(topology:Topology){
 const route=geographicRoute(topology);
 const yards:Yard[]=topology.stations.map(s=>{
  const center=route.distance(s.position_m);
  return {id:s.id,center,from:center-YARD_HALF,to:center+YARD_HALF,tracks:Math.max(1,s.tracks)};
 });
 const byId=new Map(yards.map(y=>[y.id,y]));
 const assignments=new Map<string,TrackAssignment>();
 let epoch:string|undefined,lastTime=-Infinity;
 function point(distance:number,track?:TrackAssignment):Coordinate{
  const center=route.point(distance),yard=track&&byId.get(track.stationId);
  if(!yard||!track?.index)return center;
  const edge=Math.max(0,Math.min(1,(YARD_HALF-Math.abs(distance-yard.center))/YARD_TAPER));
  const lateral=track.index*TRACK_SPACING*edge*edge*(3-2*edge);
  if(!lateral)return center;
  const yaw=trackYaw(route.point(distance-2),route.point(distance+2)),m=mercator(center);
  const x=m.x+Math.cos(yaw)*lateral*m.scale,y=m.y-Math.sin(yaw)*lateral*m.scale;
  return [x*360-180,360/Math.PI*Math.atan(Math.exp((180-y*360)*Math.PI/180))-90];
 }
 function reset(){assignments.clear();epoch=undefined;lastTime=-Infinity;}
 function place(snapshot:Snapshot):Snapshot&{trains:DisplayTrain[]}{
  if(epoch!==snapshot.epoch||snapshot.sim_time_s<lastTime)reset();
  epoch=snapshot.epoch;lastTime=snapshot.sim_time_s;
  const heads=new Map(snapshot.trains.map(t=>[t.id,route.distance(t.position_m)]));
  const overlaps=(t:Train,y:Yard)=>{
   const head=heads.get(t.id)!,tail=head-t.direction*(t.length_m+30);
   return Math.max(head,tail)>=y.from&&Math.min(head,tail)<=y.to;
  };
  for(const [id,a] of assignments){
   const train=snapshot.trains.find(t=>t.id===id);
   if(!train||!overlaps(train,byId.get(a.stationId)!))assignments.delete(id);
  }
  // Retain occupied sidings first, independent of snapshot train order or selection.
  for(const train of [...snapshot.trains].sort((a,b)=>a.id.localeCompare(b.id))){
   if(assignments.has(train.id))continue;
   const yard=yards.find(y=>overlaps(train,y));if(!yard)continue;
   const taken=new Set([...assignments.values()].filter(a=>a.stationId===yard.id).map(a=>a.index));
   let index=0;while(taken.has(index))index++;
   // If an invalid state exceeds the declared capacity, show all trains on extra display sidings.
   // This does not add dispatcher capacity; the normal conflict/quality indicators remain authoritative.
   assignments.set(train.id,{stationId:yard.id,index});
  }
  return {...snapshot,trains:snapshot.trains.map(train=>{
   const displayTrack=assignments.get(train.id),d=heads.get(train.id)!;
   if(!displayTrack?.index)return {...train,displayTrack};
   const yaw=trackYaw(point(d-6.65,displayTrack),point(d+6.65,displayTrack),train.direction);
   return {...train,displayTrack,coordinate:point(d,displayTrack),bearing_deg:((180-yaw*180/Math.PI)%360+360)%360};
  })};
 }
 function trackCount(yard:Yard){
  return Math.max(yard.tracks,...[...assignments.values()].filter(a=>a.stationId===yard.id).map(a=>a.index+1));
 }
 function features(){
  return yards.flatMap(y=>Array.from({length:trackCount(y)-1},(_,i)=>{
   const track={stationId:y.id,index:i+1},coordinates:Coordinate[]=[];
   for(let d=y.from;d<y.to;d+=20)coordinates.push(point(d,track));coordinates.push(point(y.to,track));
   return {type:'Feature' as const,properties:{station:y.id,track:i+2},geometry:{type:'LineString' as const,coordinates}};
  }));
 }
 return {route,yards,point,place,reset,trackCount,features};
}
export type StationTracks=ReturnType<typeof createStationTracks>;
