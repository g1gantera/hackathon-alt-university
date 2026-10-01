// Display geometry only. Scheduling and physical motion remain on the server.
const radians = degrees => degrees * Math.PI / 180;

function length(a, b) {
  const lat1 = radians(a[1]), lat2 = radians(b[1]);
  const h = Math.sin((lat2 - lat1) / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(radians(b[0] - a[0]) / 2) ** 2;
  return 12742000 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function prepared(points) {
  const distances=[0];
  for(let i=1;i<points.length;i++)distances.push(distances[i-1]+length(points[i-1],points[i]));
  return {points,distances};
}
function sample(line,fraction) {
  const target=Math.max(0,Math.min(1,fraction))*line.distances.at(-1);
  const i=line.distances.findIndex(d=>d>=target);
  if(i<=0)return [...line.points[0]];
  const r=(target-line.distances[i-1])/(line.distances[i]-line.distances[i-1]||1);
  return line.points[i-1].map((v,a)=>v+r*(line.points[i][a]-v));
}
// The same explicit connector polyline is drawn and used for train positions.
// These are model turnouts, not a surveyed OSM signal/turnout inventory.
export function movementGeometry(topology,section,trackId,fromTrack,toTrack) {
  const a=topology.stations.find(s=>s.id===section.from_station);
  const b=topology.stations.find(s=>s.id===section.to_station);
  const points=section.main_tracks?.find(t=>t.id===trackId)?.geometry||section.geometry;
  const line=prepared(points), span=section.length_m||Math.abs(b.position_m-a.position_m);
  const throat=Math.min(250,span/4)/span;
  const anchor=(s,id,f)=>s.track_layout?.find(t=>t.id===id)?.coordinate||sample(line,f);
  const knots=[{fraction:0,coordinate:anchor(a,fromTrack,0)},
    {fraction:throat,coordinate:sample(line,throat)}];
  for(let i=1;i<points.length-1;i++){
    const f=line.distances[i]/line.distances.at(-1);
    if(f>throat&&f<1-throat)knots.push({fraction:f,coordinate:points[i]});
  }
  knots.push({fraction:1-throat,coordinate:sample(line,1-throat)},
    {fraction:1,coordinate:anchor(b,toTrack,1)});
  return knots;
}
export function connectorFeatures(topology) {
  return {type:'FeatureCollection',features:topology.sections.flatMap(section=>
    (section.main_tracks||[{id:'1'}]).flatMap(track=>{
      const a=topology.stations.find(s=>s.id===section.from_station);
      const b=topology.stations.find(s=>s.id===section.to_station);
      return [[a,true],[b,false]].flatMap(([station,first])=>(station.track_layout||[]).map(t=>{
        const knots=movementGeometry(topology,section,track.id,first?t.id:null,first?null:t.id);
        return {type:'Feature',properties:{station:station.id,track:t.id,synthetic:true},
          geometry:{type:'LineString',coordinates:(first?knots.slice(0,2):knots.slice(-2)).map(k=>k.coordinate)}};
      }));
    }))};
}
export function createLocator(topology) {
  const stations=new Map(topology.stations.map(s=>[s.id,s]));
  const cache=new Map();
  return train=>{
    if(train.on_network===false)return null;
    if(train.station_id&&stations.has(train.station_id)){
      const s=stations.get(train.station_id);
      return s.track_layout?.find(t=>t.id===train.station_track_id)?.coordinate||s.coordinate;
    }
    const position=train.section_id?train.position_m:Math.max(0,Math.min(topology.length_m,train.position_m));
    const section=topology.sections.find(s=>s.id===train.section_id)||topology.sections.find(s=>position>=stations.get(s.from_station).position_m&&position<=stations.get(s.to_station).position_m);
    if(!section)throw new Error('Позиция поезда вне геометрии маршрута');
    const forward=train.direction!==-1;
    const from=forward?train.departure_track_id:train.arrival_track_id;
    const to=forward?train.arrival_track_id:train.departure_track_id;
    const key=JSON.stringify([section.id,train.main_track_id,from,to]);
    if(!cache.has(key))cache.set(key,movementGeometry(topology,section,train.main_track_id,from,to));
    const knots=cache.get(key);
    const a=stations.get(section.from_station).position_m,b=stations.get(section.to_station).position_m;
    const f=Math.max(0,Math.min(1,(position-a)/(b-a)));
    const i=knots.findIndex(k=>k.fraction>=f);
    if(i<=0)return [...knots[0].coordinate];
    const r=(f-knots[i-1].fraction)/(knots[i].fraction-knots[i-1].fraction);
    return knots[i-1].coordinate.map((v,axis)=>v+r*(knots[i].coordinate[axis]-v));
  };
}

export function trackIndex(train, topology) {
  const tracks = train.station_id
    ? topology.stations.find(s => s.id === train.station_id)?.track_layout
    : topology.sections.find(s => s.id === train.section_id)?.main_tracks;
  return Math.max(0, (tracks || []).findIndex(t => t.id === (train.station_track_id || train.main_track_id)));
}
