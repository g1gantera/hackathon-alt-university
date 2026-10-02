// The Astana–Kokshetau corridor in local metres, built from the same OSM geometry as the map.
// x points east, z points south, origin at the corridor centre. Chainage (model metres) maps to
// polyline distance section by section, exactly like backend/app/railway_map.py.
import type {Topology} from '../types';
import {MAT,meshBuilder,type MeshData} from './mesh';

export interface Pose{x:number;z:number;yaw:number}
export interface RouteStation{id:string;name:string;dist:number;terminal:boolean}
/** Passing loops on the −X side of the main line; offset k is the k-th loop (0 = main line). */
export interface Zone{station:number;center:number;from:number;to:number;loops:number}

export const LOOP_SPACING=5.3;
const ZONE_HALF=1100,RAMP=140,STUB=1600;

export interface Route{
 length:number;
 stations:RouteStation[];
 zones:Zone[];
 chainToDist(chainage:number):number;
 point(dist:number):[number,number];
 pose(dist:number,span?:number):Pose;
 lateral(zone:Zone|undefined,loop:number,dist:number):number;
}

export function buildRoute(topology:Topology):Route{
 const lon0=topology.stations.reduce((s,x)=>s+x.coordinate[0],0)/topology.stations.length;
 const lat0=topology.stations.reduce((s,x)=>s+x.coordinate[1],0)/topology.stations.length;
 const kx=111320*Math.cos(lat0*Math.PI/180),kz=110540;
 const project=([lon,lat]:[number,number]):[number,number]=>[(lon-lon0)*kx,-(lat-lat0)*kz];
 // Join the sections into one control polyline, remembering where each section ends.
 const control:[number,number][]=[],sectionEnds:number[]=[];
 for(const section of topology.sections){
  for(const p of section.geometry.map(project)){
   const last=control.at(-1);
   if(!last||Math.hypot(p[0]-last[0],p[1]-last[1])>.5)control.push(p);
  }
  sectionEnds.push(control.length-1);
 }
 // Centripetal Catmull-Rom keeps curves smooth without overshooting on unevenly spaced OSM points.
 const xs:number[]=[],zs:number[]=[],controlSample:number[]=[];
 const at=(i:number)=>control[Math.max(0,Math.min(control.length-1,i))];
 for(let i=0;i<control.length-1;i++){
  controlSample[i]=xs.length;
  const p0=at(i-1),p1=at(i),p2=at(i+1),p3=at(i+2);
  const knot=(a:[number,number],b:[number,number])=>Math.max(1e-3,Math.hypot(b[0]-a[0],b[1]-a[1])**.5);
  const t1=knot(p0,p1),t2=t1+knot(p1,p2),t3=t2+knot(p2,p3);
  const steps=Math.max(1,Math.ceil(Math.hypot(p2[0]-p1[0],p2[1]-p1[1])/8));
  for(let s=0;s<steps;s++){
   const t=t1+(t2-t1)*s/steps;
   const lerp=(a:[number,number],b:[number,number],ta:number,tb:number):[number,number]=>{const w=(t-ta)/(tb-ta||1);return [a[0]+(b[0]-a[0])*w,a[1]+(b[1]-a[1])*w];};
   const a1=lerp(p0,p1,0,t1),a2=lerp(p1,p2,t1,t2),a3=lerp(p2,p3,t2,t3);
   const b1=lerp(a1,a2,0,t2),b2=lerp(a2,a3,t1,t3);
   const c=lerp(b1,b2,t1,t2);
   xs.push(c[0]);zs.push(c[1]);
  }
 }
 controlSample[control.length-1]=xs.length;
 xs.push(control.at(-1)![0]);zs.push(control.at(-1)![1]);
 const cum=new Float64Array(xs.length);
 for(let i=1;i<xs.length;i++)cum[i]=cum[i-1]+Math.hypot(xs[i]-xs[i-1],zs[i]-zs[i-1]);
 const length=cum[cum.length-1];
 const sectionDist=[0,...sectionEnds.map(i=>cum[controlSample[i]])];
 const stationPos=topology.stations.map(s=>s.position_m);

 function chainToDist(chainage:number){
  // Beyond the terminals the line continues as a straight stub for consists standing there.
  if(chainage<=0)return chainage;
  if(chainage>=topology.length_m)return length+(chainage-topology.length_m);
  let i=topology.sections.length-1;
  for(let k=0;k<topology.sections.length;k++)if(chainage<stationPos[k+1]){i=k;break;}
  const fraction=(chainage-stationPos[i])/topology.sections[i].length_m;
  return sectionDist[i]+Math.max(0,Math.min(1,fraction))*(sectionDist[i+1]-sectionDist[i]);
 }
 function point(dist:number):[number,number]{
  const n=xs.length;
  if(dist<=0){const l=cum[1]||1;return [xs[0]+(xs[0]-xs[1])/l*-dist,zs[0]+(zs[0]-zs[1])/l*-dist];}
  if(dist>=length){const l=(cum[n-1]-cum[n-2])||1,e=dist-length;return [xs[n-1]+(xs[n-1]-xs[n-2])/l*e,zs[n-1]+(zs[n-1]-zs[n-2])/l*e];}
  let lo=0,hi=n-1;
  while(hi-lo>1){const mid=(lo+hi)>>1;if(cum[mid]<=dist)lo=mid;else hi=mid;}
  const t=(dist-cum[lo])/((cum[hi]-cum[lo])||1);
  return [xs[lo]+(xs[hi]-xs[lo])*t,zs[lo]+(zs[hi]-zs[lo])*t];
 }
 function pose(dist:number,span=6):Pose{
  const p=point(dist),a=point(dist-span/2),b=point(dist+span/2);
  return {x:p[0],z:p[1],yaw:Math.atan2(b[0]-a[0],b[1]-a[1])};
 }
 const stations=topology.stations.map((s,i)=>({id:s.id,name:s.name,dist:chainToDist(s.position_m),terminal:i===0||i===topology.stations.length-1}));
 const zones=stations.map((s,i)=>({station:i,center:s.dist,from:s.dist-ZONE_HALF,to:s.dist+ZONE_HALF,loops:s.terminal?4:1}));
 function lateral(zone:Zone|undefined,loop:number,dist:number){
  if(!zone||!loop||dist<=zone.from||dist>=zone.to)return 0;
  const edge=Math.min(dist-zone.from,zone.to-dist)/RAMP,k=Math.min(1,edge);
  return -LOOP_SPACING*loop*k*k*(3-2*k);
 }
 return {length,stations,zones,chainToDist,point,pose,lateral};
}

export interface Chunk{mesh:MeshData;origin:[number,number];center:[number,number];radius:number}

/** Ballast, rails (bright head, rusty web) and, on the main line, the overhead contact wires. */
export function buildTrackChunks(route:Route):Chunk[]{
 const chunks:Chunk[]=[];
 const strip=(from:number,to:number,offset:(d:number)=>number,wires:boolean)=>{
  for(let start=from;start<to;start+=1500){
   const end=Math.min(to,start+1500),b=meshBuilder(),step=5;
   const origin=route.point(start);
   const frame=(d:number)=>{const p=route.pose(d,4),lat=offset(d);return {x:p.x+Math.cos(p.yaw)*lat-origin[0],z:p.z-Math.sin(p.yaw)*lat-origin[1],c:Math.cos(p.yaw),s:Math.sin(p.yaw)};};
   const at=(f:ReturnType<typeof frame>,x:number,y:number):[number,number,number]=>[f.x+f.c*x,y,f.z-f.s*x];
   let prev=frame(start);
   let minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity;
   for(let d=start+step;d<=end+1e-6;d+=step){
    const next=frame(Math.min(d,end));
    const band=(x1:number,y1:number,x2:number,y2:number,color:string,mat:number=MAT.matte)=>b.quad(at(prev,x1,y1),at(next,x1,y1),at(next,x2,y2),at(prev,x2,y2),color,mat);
    band(-3.4,-.8,-1.85,-.25,'#736d64');band(-1.85,-.25,1.85,-.25,'#86807a');band(1.85,-.25,3.4,-.8,'#736d64');
    for(const r of [-.79,.79]){
     band(r-.037,0,r+.037,0,'#c9cdcf',MAT.metal);
     band(r-.037,-.17,r-.037,0,'#5f4f43');band(r+.037,0,r+.037,-.17,'#5f4f43');
    }
    if(wires){
     band(-.03,6.05,.03,6.05,'#3a3d3f',MAT.metal);band(0,6.02,0,6.08,'#3a3d3f',MAT.metal);
     band(-.03,7.35,.03,7.35,'#3a3d3f',MAT.metal);
    }
    for(const f of [next]){minX=Math.min(minX,f.x);maxX=Math.max(maxX,f.x);minZ=Math.min(minZ,f.z);maxZ=Math.max(maxZ,f.z);}
    prev=next;
   }
   const cx=(minX+maxX)/2,cz=(minZ+maxZ)/2;
   chunks.push({mesh:b.finish(),origin,center:[origin[0]+cx,origin[1]+cz],radius:Math.hypot(maxX-minX,maxZ-minZ)/2+10});
  }
 };
 strip(-STUB,route.length+STUB,()=>0,true);
 for(const zone of route.zones)for(let k=1;k<=zone.loops;k++)strip(zone.from,zone.to,d=>route.lateral(zone,k,d),false);
 return chunks;
}

export function createSleeper():MeshData{
 const b=meshBuilder();
 b.box([2.7,.19,.28],[0,-.115,0],'#a9a69d',MAT.matte);
 for(const s of [-1,1])for(const dx of [-.11,.11])b.box([.07,.05,.16],[s*.79+dx,-.005,0],'#3b3632',MAT.metal);
 return b.finish();
}

/** Concrete catenary mast standing at local x = 0 with its cantilever reaching +X over the track. */
export function createMast(reach=3.4):MeshData{
 const b=meshBuilder();
 b.box([.7,.5,.7],[0,-.55,0],'#8f8c86',MAT.matte);
 b.cylinder(.2,9.4,[0,4.4,0],'#a19e96',MAT.matte,10,[0,0,0],.13);
 b.box([reach+.3,.09,.09],[reach/2,7.35,0],'#5c6266',MAT.metal);
 b.box([reach-.2,.07,.07],[reach/2,6.25,0],'#5c6266',MAT.metal,[0,0,-.18]);
 b.cylinder(.06,.5,[.25,7.1,0],'#7a5a3c',MAT.paint,8,[0,0,Math.PI/2]);
 b.box([.05,1.3,.05],[reach,6.7,0],'#5c6266',MAT.metal);
 return b.finish();
}

export function createGround(size:number):MeshData{
 const b=meshBuilder(),h=size/2;
 b.quad([-h,-.8,-h],[-h,-.8,h],[h,-.8,h],[h,-.8,-h],'#8d8a5c',MAT.matte);
 return b.finish();
}
