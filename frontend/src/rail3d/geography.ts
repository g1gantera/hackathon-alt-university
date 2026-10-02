import type {Topology} from '../types';

export type Coordinate=[number,number];
const radians=Math.PI/180;

/** Same section-by-section, haversine-weighted interpolation as railway_map.locate. */
export function geographicRoute(topology:Topology){
 const stations=new Map(topology.stations.map(s=>[s.id,s]));
 let total=0;
 const sections=topology.sections.map(section=>{
  const points=section.geometry.filter((p,i,a)=>!i||p[0]!==a[i-1][0]||p[1]!==a[i-1][1]);
  if(points.length<2)throw Error('Rail section must contain a nonzero polyline');
  const distances=[0];
  for(let i=1;i<points.length;i++){
   const [lon1,lat1]=points[i-1].map(v=>v*radians),[lon2,lat2]=points[i].map(v=>v*radians);
   const h=Math.sin((lat2-lat1)/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin((lon2-lon1)/2)**2;
   distances.push(distances[i-1]+12742000*Math.asin(Math.min(1,Math.sqrt(h))));
  }
  const start=total,length=distances.at(-1)!;total+=length;
  return {points,distances,start,length,chain:stations.get(section.from_station)!.position_m,
   endChain:stations.get(section.to_station)!.position_m,modelLength:section.length_m};
 });
 if(!sections.length)throw Error('Rail topology has no sections');
 function distance(chain:number){
  const position=Math.max(0,Math.min(topology.length_m,chain));
  const section=sections.find(s=>position<s.endChain)??sections.at(-1)!;
  const fraction=Math.max(0,Math.min(1,(position-section.chain)/section.modelLength));
  return section.start+fraction*section.length;
 }
 function point(d:number):Coordinate{
  // Continue the terminal tangent so carriages behind a locomotive do not collapse at the endpoint.
  const s=sections.find(s=>d<s.start+s.length)??sections.at(-1)!;
  const target=d-s.start;
  let lo=0,hi=s.points.length-1;
  while(hi-lo>1){const mid=(hi+lo)>>1;if(s.distances[mid]<=target)lo=mid;else hi=mid;}
  const a=s.points[lo],b=s.points[lo+1],t=(target-s.distances[lo])/(s.distances[lo+1]-s.distances[lo]);
  return [a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
 }
 return {distance,point,length:total};
}

/** MapLibre's normalized Web Mercator coordinates and metre scale. */
export function mercator([lon,lat]:Coordinate){
 return {x:(180+lon)/360,y:(180-180/Math.PI*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360)))/360,
  scale:1/(2*Math.PI*6371008.8*Math.cos(lat*radians))};
}

export function trackYaw(a:Coordinate,b:Coordinate,direction=1){
 const p=mercator(a),q=mercator(b);
 return Math.atan2(q.x-p.x,q.y-p.y)+(direction<0?Math.PI:0);
}

/** Multiply in JS double precision before conversion: float32 world origins jitter at train zoom. */
export function modelMatrix(projection:ArrayLike<number>,coordinate:Coordinate,yaw:number,altitude=0){
 const {x,y,scale:s}=mercator(coordinate),c=Math.cos(yaw),n=Math.sin(yaw);
 // Model +Y is up, +Z is the front; Mercator +Y points south and +Z points up.
 const model=[s*c,-s*n,0,0, 0,0,s,0, s*n,s*c,0,0, x,y,altitude*s,1];
 const result=new Float32Array(16);
 for(let col=0;col<4;col++)for(let row=0;row<4;row++){
  let sum=0;for(let k=0;k<4;k++)sum+=projection[k*4+row]*model[col*4+k];
  result[col*4+row]=sum;
 }
 return result;
}
