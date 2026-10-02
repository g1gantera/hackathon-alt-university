import {mercator,trackYaw,type Coordinate} from './geography.ts';
import {meshBuilder,MAT,type Vec3} from './mesh.ts';
import {YARD_HALF,type StationTracks,type TrackAssignment} from './stationTracks.ts';

export const RAIL_ALTITUDE=.8;
export interface RailChunk {id:string;from:number;to:number;track?:TrackAssignment;origin:Coordinate;bounds:[number,number,number,number]}

export function railChunks(layout:StationTracks):RailChunk[]{
 const result:RailChunk[]=[];
 function add(from:number,to:number,track?:TrackAssignment){
  for(let d=from;d<to;d+=250){
   const end=Math.min(to,d+250),coordinates:Coordinate[]=[];
   for(let x=d;x<end;x+=20)coordinates.push(layout.point(x,track));coordinates.push(layout.point(end,track));
   result.push({id:`${track?.stationId??'main'}:${track?.index??0}:${d}`,from:d,to:end,track,
    origin:layout.point((d+end)/2,track),bounds:[Math.min(...coordinates.map(p=>p[0])),Math.min(...coordinates.map(p=>p[1])),Math.max(...coordinates.map(p=>p[0])),Math.max(...coordinates.map(p=>p[1]))]});
  }
 }
 add(-YARD_HALF,layout.route.length+YARD_HALF);
 for(const yard of layout.yards)for(let i=1;i<layout.trackCount(yard);i++)add(yard.from,yard.to,{stationId:yard.id,index:i});
 return result;
}

/** Visible chunks only: steel rails, ballast shoulder and sleepers in metre-scale local coordinates. */
export function railMesh(chunk:RailChunk,layout:StationTracks,sleepers:boolean){
 const b=meshBuilder(),origin=mercator(chunk.origin);
 function frame(d:number){
  const p=mercator(layout.point(d,chunk.track));
  const yaw=trackYaw(layout.point(d-1,chunk.track),layout.point(d+1,chunk.track));
  return {x:(p.x-origin.x)/origin.scale,z:(p.y-origin.y)/origin.scale,c:Math.cos(yaw),s:Math.sin(yaw),yaw};
 }
 const point=(f:ReturnType<typeof frame>,x:number,y:number):Vec3=>[f.x+f.c*x,y,f.z-f.s*x];
 let previous=frame(chunk.from);
 for(let d=chunk.from;d<chunk.to;d+=4){
  const next=frame(Math.min(chunk.to,d+4));
  const band=(x1:number,y1:number,x2:number,y2:number,color:string,material:number=MAT.matte)=>b.quad(point(previous,x1,y1),point(next,x1,y1),point(next,x2,y2),point(previous,x2,y2),color,material);
  band(-2,-.6,-1.5,-.28,'#736d64');band(-1.5,-.28,1.5,-.28,'#888078');band(1.5,-.28,2,-.6,'#736d64');
  for(const r of [-.79,.79]){
   band(r-.055,0,r+.055,0,'#d4d8dc',MAT.metal);
   band(r-.055,-.18,r-.055,0,'#65564b');band(r+.055,0,r+.055,-.18,'#65564b');
  }
  previous=next;
 }
 if(sleepers)for(let d=Math.ceil(chunk.from/.65)*.65;d<chunk.to;d+=.65){
  const p=frame(d);b.box([2.7,.16,.24],[p.x,-.18,p.z],'#a5a299',MAT.matte,[0,p.yaw,0]);
 }
 return b.finish();
}
