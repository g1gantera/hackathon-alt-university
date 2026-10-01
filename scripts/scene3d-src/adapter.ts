/**
 * Read-only presentation adapter for the era graph and engine snapshots.
 * No routing, reservations, dispatch decisions, requests or state mutations.
 * Raw graph geometry is [longitude, latitude]; engine/map positions are [latitude, longitude].
 */
import type {TrainMotionFrame} from './motion.ts';

export type LonLat = [number, number];
export type LatLon = [number, number];
export type Arc = [number, 0 | 1];
export type Tags = Record<string, unknown>;

export interface EraBlock {
 resource:string; edge:number; direction:0|1; index:number;
 start_m:number; end_m:number; occupied?:boolean; geometry:LatLon[];
}
export interface EraTrain {
 id:string; name:string; state:string; reason:string;
 position:LatLon; edge:number; direction:0|1;
 distance_m:number; total_m:number; speed_kmh:number; importance:number;
 route:Arc[]; route_edges:number[];
 spec:{origin:number; destination:number; length_m:number; mass_t:number; [key:string]:unknown};
 occupied_edges:number[]; reserved_edges:number[];
 occupied_blocks?:EraBlock[]; reserved_blocks?:EraBlock[];
 origin?:string; destination?:string; [key:string]:unknown;
}
export interface EraSignal {
 id:string; edge:number; direction:0|1; vertex:number|null;
 position:LatLon; aspect:'red'|'yellow'|'green'; failed:boolean;
 owner:string|null; occupied_by:string|null; reason:string; [key:string]:unknown;
}
export interface EraSnapshot {
 run_id:string; version:number; plan_version:number; sim_time:number;
 server_time:number; running:boolean; simulation_speed:number;
 trains:EraTrain[]; signals:EraSignal[];
 switches:{vertex:number; owner:string; position:[number|null,number]; locked:boolean}[];
 incidents:Record<string,unknown>[]; safety_errors:unknown[]; [key:string]:unknown;
}
/** GET /api/network supplies metadata and station labels, not graph geometry. */
export interface EraNetworkInfo {
 stats:Record<string,unknown>;
 demo:{origin:number;destination:number;[key:string]:unknown};
 stations:{vertex:number;name:string;name_en:string;snap_dist_m:number}[];
 hashes:Record<string,string>; excluded_synthetic_edges:number; assumptions:string[];
}
export interface RawNetwork {
 vertices:{id:number;lon:number;lat:number;[key:string]:unknown}[];
 edges:{id:number;u:number;v:number;way:number;category:string;length_m:number;geometry:LonLat[]}[];
 ways:{osm_id:number;synthetic?:boolean;tags:Tags;[key:string]:unknown}[];
 stations:{osm_id:number;type:string;name:string;name_en:string;lon:number;lat:number;vertex:number|null;[key:string]:unknown}[];
 stats:Record<string,unknown>; attribution?:string; [key:string]:unknown;
}
export interface CompactRail {
 Q:number; cats:string[]; stats:Record<string,unknown>; v:LonLat[];
 e:[number,number,number,number,number,number[]][];
 w:[number,Tags][];
 s:[number,string,string,string,number,number,number|null,string|null,number][];
}
export interface RailEdge {
 id:number; u:number; v:number; way:number; category:string;
 length_m:number; geometry:LonLat[]; synthetic:boolean; tags:Tags;
}
export interface RailStation {
 id:number; name:string; name_en:string; type:string; coordinate:LonLat; vertex:number|null;
}
export interface RailNetwork {
 vertices:LonLat[]; edges:RailEdge[]; stations:RailStation[];
 stats:Record<string,unknown>; precision:'exact'|'compact'; attribution:string;
}
export interface RailPose {x:number; z:number; angle:number; coordinate:LonLat; position:LatLon}
export interface RoutePose extends RailPose {edge:number;direction:0|1;arcIndex:number;offset_m:number}
export interface RouteSampler {
 (chainage:number, extend?:boolean):RoutePose;
 readonly length_m:number; readonly signature:string; readonly route:ReadonlyArray<Arc>;
}

const EARTH_DIAMETER_M = 12742017.6; // backend.network.hav uses the same radius.
const LATITUDE = 52.2, LONGITUDE = 70.5, METERS_PER_DEGREE = 111320;
const clamp = (v:number, low:number, high:number) => Math.max(low, Math.min(high, v));
const edgeLengths = new WeakMap<RailEdge, number[]>();

function coordinate(value:LonLat):LonLat {
 if (!Array.isArray(value) || value.length !== 2 || !value.every(Number.isFinite)) throw new Error('Invalid railway coordinate');
 return [value[0],value[1]];
}
export function fromLatLon(value:LatLon):LonLat {return coordinate([value[1],value[0]]);}
export function toLatLon(value:LonLat):LatLon {const p=coordinate(value);return [p[1],p[0]];}
/** East is +X, north is -Z. This projection is solely for drawing. */
export function project(value:LonLat):[number,number] {
 return [(value[0]-LONGITUDE)*METERS_PER_DEGREE*Math.cos(LATITUDE*Math.PI/180),
  -(value[1]-LATITUDE)*METERS_PER_DEGREE];
}
export function projectLatLon(value:LatLon):[number,number] {return project(fromLatLon(value));}
export function geographicDistance(a:LonLat,b:LonLat):number {
 const p=a[1]*Math.PI/180,q=b[1]*Math.PI/180;
 const h=Math.sin((q-p)/2)**2+Math.cos(p)*Math.cos(q)*Math.sin((b[0]-a[0])*Math.PI/360)**2;
 return EARTH_DIAMETER_M*Math.asin(Math.min(1,Math.sqrt(h)));
}

/** Decode exact, unchanged network.json copied into the visual asset directory. */
export function decodeNetwork(raw:RawNetwork):RailNetwork {
 const vertices=raw.vertices.map(v=>coordinate([v.lon,v.lat]));
 const edges=raw.edges.map((e,index)=>{
  if(e.id!==index)throw new Error('Graph edge IDs must preserve source array indices');
  const way=raw.ways[e.way];
  if(!way)throw new Error(`Missing graph way ${e.way}`);
  return {...e,geometry:e.geometry.map(coordinate),synthetic:!!way.synthetic,tags:{...way.tags}};
 });
 return {vertices,edges,stations:raw.stations.map(s=>({id:s.osm_id,type:s.type,name:s.name,name_en:s.name_en,
  coordinate:coordinate([s.lon,s.lat]),vertex:s.vertex})),stats:{...raw.stats},precision:'exact',
  attribution:raw.attribution||'© OpenStreetMap contributors'};
}

/**
 * Decode the EXISTING map_data.js window.RAIL structure for context rendering.
 * Its integer edge lengths and 1/Q degree coordinates are lossy. Supply exact
 * source data when matching backend chainage; do not mistake compact geometry
 * for exact Network.position output, especially on long multi-edge routes.
 */
export function decodeCompactRail(raw:CompactRail,exact?:RawNetwork):RailNetwork {
 if(exact)return decodeNetwork(exact);
 if(!Number.isFinite(raw.Q)||raw.Q<=0)throw new Error('Invalid compact graph scale');
 const edges=raw.e.map(([u,v,category,way,length,deltas],id)=>{
  if(deltas.length<4||deltas.length%2)throw new Error(`Invalid compact edge ${id}`);
  const geometry:LonLat[]=[];let x=0,y=0;
  for(let i=0;i<deltas.length;i+=2){x+=deltas[i];y+=deltas[i+1];geometry.push(coordinate([x/raw.Q,y/raw.Q]));}
  const sourceWay=raw.w[way];
  return {id,u,v,way,category:raw.cats[category],length_m:length,geometry,
   synthetic:(sourceWay?.[0]??0)<0,tags:{...(sourceWay?.[1]??{})}};
 });
 return {vertices:raw.v.map(([x,y])=>coordinate([x/raw.Q,y/raw.Q])),edges,
  stations:raw.s.map(([id,type,name,name_en,x,y,vertex])=>({id,type,name,name_en,coordinate:coordinate([x/raw.Q,y/raw.Q]),vertex})),
  stats:{...raw.stats},precision:'compact',attribution:'© OpenStreetMap contributors'};
}

function lengths(edge:RailEdge):number[] {
 let result=edgeLengths.get(edge);
 if(!result){
  result=[0];
  for(let i=1;i<edge.geometry.length;i++)result.push(result[i-1]+geographicDistance(edge.geometry[i-1],edge.geometry[i]));
  edgeLengths.set(edge,result);
 }
 return result;
}
function rightIndex(values:number[],target:number):number {
 let low=0,high=values.length;
 while(low<high){const middle=(low+high)>>>1;if(values[middle]<=target)low=middle+1;else high=middle;}
 return low-1;
}
function edgeOf(network:RailNetwork,arc:Arc):RailEdge {
 const edge=network.edges[arc[0]];
 if(!edge||edge.id!==arc[0]||(arc[1]!==0&&arc[1]!==1))throw new Error(`Unknown directed graph edge ${arc}`);
 if(edge.length_m<=0||edge.geometry.length<2||!Number.isFinite(edge.length_m))throw new Error(`Edge ${edge.id} needs exact positive source length`);
 return edge;
}
/** Exactly mirror network.position interpolation; returned position is [lat,lon]. */
export function sampleArc(network:RailNetwork,arc:Arc,offset:number):RailPose {
 if(!Number.isFinite(offset))throw new Error('Train offset must be finite');
 const edge=edgeOf(network,arc),cumulative=lengths(edge),fraction=clamp(offset/edge.length_m,0,1);
 const d=(arc[1]?1-fraction:fraction)*cumulative[cumulative.length-1];
 const index=clamp(rightIndex(cumulative,d),0,edge.geometry.length-2);
 const ratio=(d-cumulative[index])/Math.max(1e-9,cumulative[index+1]-cumulative[index]);
 const a=edge.geometry[index],b=edge.geometry[index+1];
 const location:LonLat=[a[0]+(b[0]-a[0])*ratio,a[1]+(b[1]-a[1])*ratio];
 const [x,z]=project(location),start=project(a),end=project(b),sign=arc[1]?-1:1;
 return {x,z,angle:Math.atan2((end[0]-start[0])*sign,(end[1]-start[1])*sign),coordinate:location,position:toLatLon(location)};
}
export function routeSignature(route:ReadonlyArray<Arc>):string {return route.map(a=>`${a[0]}:${a[1]}`).join('|');}

/** Follow only the engine-supplied directed arcs; never search for a path. */
export function createRouteSampler(network:RailNetwork,sourceRoute:ReadonlyArray<Arc>):RouteSampler {
 if(!sourceRoute.length)throw new Error('A train route must contain at least one directed edge');
 const route=sourceRoute.map(a=>[a[0],a[1]] as Arc),prefix=[0];
 for(let i=0;i<route.length;i++){
  const edge=edgeOf(network,route[i]);
  if(i){
   const prior=edgeOf(network,route[i-1]);
   const end=route[i-1][1]?prior.u:prior.v,start=route[i][1]?edge.v:edge.u;
   if(end!==start)throw new Error('Disconnected supplied route; visual crossings do not create graph connections');
  }
  prefix.push(prefix[prefix.length-1]+edge.length_m);
 }
 const total=prefix[prefix.length-1];
 const sampler=((chainage:number,extend=false):RoutePose=>{
  if(!Number.isFinite(chainage))throw new Error('Train chainage must be finite');
  const bounded=clamp(chainage,0,total),index=clamp(rightIndex(prefix,bounded),0,route.length-1),arc=route[index];
  const offset=clamp(bounded-prefix[index],0,network.edges[arc[0]].length_m);
  const pose=sampleArc(network,arc,offset);
  if(extend&&chainage!==bounded){
   // Visual carriages can trail beyond the first route vertex while a train is
   // staged. This tangent extension adds no graph edge or operating resource.
   const extra=chainage-bounded;pose.x+=Math.sin(pose.angle)*extra;pose.z+=Math.cos(pose.angle)*extra;
   pose.coordinate=[LONGITUDE+pose.x/(METERS_PER_DEGREE*Math.cos(LATITUDE*Math.PI/180)),LATITUDE-pose.z/METERS_PER_DEGREE];
   pose.position=toLatLon(pose.coordinate);
  }
  return {...pose,edge:arc[0],direction:arc[1],arcIndex:index,offset_m:offset};
 }) as RouteSampler;
 Object.defineProperties(sampler,{length_m:{value:total},signature:{value:routeSignature(route)},route:{value:route}});
 return sampler;
}

/** Draw a vehicle between its own bogie positions, with no change to head chainage. */
export function vehiclePose(sample:RouteSampler,center:number,wheelbase:number):RailPose {
 const front=sample(center+wheelbase/2,true),rear=sample(center-wheelbase/2,true);
 const location:LonLat=[(front.coordinate[0]+rear.coordinate[0])/2,(front.coordinate[1]+rear.coordinate[1])/2];
 return {x:(front.x+rear.x)/2,z:(front.z+rear.z)/2,angle:Math.atan2(front.x-rear.x,front.z-rear.z),
  coordinate:location,position:toLatLon(location)};
}
export function commonRoutePrefixLength(network:RailNetwork,a:ReadonlyArray<Arc>,b:ReadonlyArray<Arc>):number {
 let length=0;
 for(let i=0;i<Math.min(a.length,b.length);i++){
  if(a[i][0]!==b[i][0]||a[i][1]!==b[i][1])break;
  length+=edgeOf(network,a[i]).length_m;
 }
 return length;
}
/** A future reroute is safe to animate only if the already-travelled prefix survived. */
export function canInterpolateTrain(network:RailNetwork,before:EraTrain,after:EraTrain):boolean {
 if(before.id!==after.id||!before.route.length||!after.route.length||!Number.isFinite(before.distance_m)||!Number.isFinite(after.distance_m)||before.distance_m<0||after.distance_m<before.distance_m)return false;
 if(routeSignature(before.route)===routeSignature(after.route))return true;
 const prior=edgeOf(network,before.route[0]),next=edgeOf(network,after.route[0]);
 const priorOrigin=before.route[0][1]?prior.v:prior.u,nextOrigin=after.route[0][1]?next.v:next.u;
 return priorOrigin===nextOrigin&&commonRoutePrefixLength(network,before.route,after.route)>=before.distance_m-0.01;
}

/** Signal position/aspect stay authoritative; nearest segment supplies its drawing orientation only. */
export function signalPose(network:RailNetwork,signal:EraSignal):RailPose {
 const location=fromLatLon(signal.position),[x,z]=project(location),edge=network.edges[signal.edge];
 let angle=0,best=Infinity;
 if(edge)for(let i=1;i<edge.geometry.length;i++){
  const a=project(edge.geometry[i-1]),b=project(edge.geometry[i]),dx=b[0]-a[0],dz=b[1]-a[1];
  const t=clamp(((x-a[0])*dx+(z-a[1])*dz)/(dx*dx+dz*dz||1),0,1),d=(x-a[0]-t*dx)**2+(z-a[1]-t*dz)**2;
  if(d<best){best=d;const sign=signal.direction?-1:1;angle=Math.atan2(dx*sign,dz*sign);}
 }
 return {x,z,angle,coordinate:location,position:[...signal.position]};
}

/** Normalize names only for visual interpolation; the era snapshot is never modified. */
export function toMotionFrame(snapshot:EraSnapshot):TrainMotionFrame {
 return {epoch:snapshot.run_id,sim_time_s:snapshot.sim_time,running:snapshot.running,speed:snapshot.simulation_speed,
  update_interval_ms:500,trains:snapshot.trains.map(t=>({id:t.id,position_m:t.distance_m,direction:1,
   status:t.state==='running'||t.state==='braking'?'moving':t.state}))};
}
