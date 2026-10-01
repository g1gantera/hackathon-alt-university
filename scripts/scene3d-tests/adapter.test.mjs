import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {decodeNetwork,decodeCompactRail,fromLatLon,toLatLon,project,createRouteSampler,sampleArc,
 routeSignature,commonRoutePrefixLength,canInterpolateTrain,signalPose,vehiclePose,toMotionFrame} from '../scene3d-src/adapter.ts';

const close=(a,b,tolerance=1e-9)=>assert.ok(Math.abs(a-b)<=tolerance,`${a} differs from ${b}`);
const raw={
 vertices:[{id:0,lon:70,lat:52},{id:1,lon:70.02,lat:52.01},{id:2,lon:70.03,lat:52.02},{id:3,lon:70.04,lat:52.01}],
 edges:[
  {id:0,u:0,v:1,way:0,category:'main',length_m:1500.25,geometry:[[70,52],[70,52.01],[70.02,52.01]]},
  {id:1,u:2,v:1,way:0,category:'siding',length_m:750.5,geometry:[[70.03,52.02],[70.025,52.015],[70.02,52.01]]},
  {id:2,u:1,v:3,way:1,category:'yard',length_m:2000,geometry:[[70.02,52.01],[70.04,52.01]]},
 ],
 ways:[{osm_id:12,synthetic:false,tags:{railway:'rail'}},{osm_id:-9,synthetic:true,tags:{railway:'rail'}}],
 stations:[{osm_id:77,type:'station',name:'Station',name_en:'Station',lon:70.02,lat:52.01,vertex:1}],stats:{edges:3},
};
const route=[[0,0],[1,1]],network=decodeNetwork(raw);
const train=(arcs=route,distance=100)=>({id:'A',route:arcs,distance_m:distance,state:'running',position:[52,70],speed_kmh:35});

test('decoder preserves exact graph IDs, synthetic annotations and coordinate conventions without mutation',()=>{
 const before=JSON.stringify(raw);const decoded=decodeNetwork(raw);
 assert.equal(decoded.precision,'exact');assert.deepEqual(decoded.vertices[0],[70,52]);
 assert.equal(decoded.edges[1].length_m,750.5);assert.equal(decoded.edges[2].synthetic,true);
 assert.deepEqual(fromLatLon([52,70]),[70,52]);assert.deepEqual(toLatLon([70,52]),[52,70]);
 createRouteSampler(decoded,route)(500);assert.equal(JSON.stringify(raw),before);
 decoded.edges[0].geometry[0][0]=999;assert.equal(raw.edges[0].geometry[0][0],70);
});

test('route samples match backend Network.locate/position fixtures across bends and reversed edges',()=>{
 const sample=createRouteSampler(network,route);
 // Fixed outputs from era backend.network.Network, using the fixture graph above.
 for(const [distance,lat,lon,arcIndex,edge,direction] of [
  [0,52,70,0,0,0],
  [375.0625,52.005577619650936,70,0,0,0],
  [750.125,52.01,70.00187683897443,0,0,0],
  [1500.25,52.01,70.02,1,1,1],
  [1875.5,52.01499992324591,70.02499992324591,1,1,1],
  [2250.75,52.02,70.03,1,1,1],
 ]){
  const p=sample(distance);close(p.position[0],lat);close(p.position[1],lon);
  assert.equal(p.arcIndex,arcIndex);assert.equal(p.edge,edge);assert.equal(p.direction,direction);
  const [x,z]=project([lon,lat]);close(p.x,x,1e-7);close(p.z,z,1e-7);
 }
 assert.equal(sample.length_m,2250.75);assert.equal(sample.signature,'0:0|1:1');
 assert.deepEqual(sample(-20),sample(0));assert.deepEqual(sample(9999),sample(sample.length_m));
 assert.throws(()=>sample(NaN),/finite/);
});

test('the supplied route is followed without inventing connectivity at crossings',()=>{
 assert.throws(()=>createRouteSampler(network,[[0,0],[1,0]]),/Disconnected/);
 assert.throws(()=>createRouteSampler(network,[[999,0]]),/Unknown/);
 assert.throws(()=>createRouteSampler(network,[]),/at least one/);
 const forward=sampleArc(network,[0,0],250),reverse=sampleArc(network,[0,1],1500);
 assert.ok(forward.angle!==reverse.angle);
 const reversed=createRouteSampler(network,[[1,0],[0,1]]);
 assert.deepEqual(reversed(0).position,[52.02,70.03]);
 assert.deepEqual(reversed(reversed.length_m).position,[52,70]);
});

test('terminal drawing extensions preserve spaced carriages without changing route or bounded head',()=>{
 for(const arcs of [route,[[1,0],[0,1]]]){
  const sample=createRouteSampler(network,arcs),before=routeSignature(arcs);
  for(const endpoint of [0,sample.length_m]){
   const a=sample(endpoint, true),b=sample(endpoint+(endpoint===0?-50:50),true);
   close(Math.hypot(a.x-b.x,a.z-b.z),50,1e-7);
  }
  assert.notDeepEqual(vehiclePose(sample,-20,14).position,vehiclePose(sample,-45,14).position);
  assert.equal(routeSignature(arcs),before);assert.deepEqual(sample(-50),sample(0));
 }
});

test('rerouting retains interpolation only across a preserved already-travelled prefix',()=>{
 const changed=[[0,0],[2,0]];
 assert.equal(commonRoutePrefixLength(network,route,changed),1500.25);
 assert.equal(canInterpolateTrain(network,train(route,1400),train(changed,1600)),true);
 assert.equal(canInterpolateTrain(network,train(route,1800),train(changed,1900)),false);
 assert.equal(canInterpolateTrain(network,train(route,100),train(route,99)),false);
 assert.equal(canInterpolateTrain(network,train(route,100),train(route,120)),true);
 assert.equal(canInterpolateTrain(network,train(route,0),train([[1,0]],20)),false);
 assert.equal(canInterpolateTrain(network,train([],0),train([],0)),false);
});

test('signal placement reads existing latitude/longitude positions and preserves backend aspect',()=>{
 const signal={id:'s-1',edge:1,direction:1,position:[52.015,70.025],aspect:'yellow'};
 const before=JSON.stringify(signal),pose=signalPose(network,signal),reverse=signalPose(network,{...signal,direction:0});
 assert.deepEqual(pose.position,signal.position);assert.deepEqual(pose.coordinate,[70.025,52.015]);
 close(Math.cos(pose.angle-reverse.angle),-1);assert.equal(JSON.stringify(signal),before);
});

test('era state normalization changes presentation field names only, never edge direction or engine state',()=>{
 const state={run_id:'era-run',version:18,sim_time:5.2,simulation_speed:15,running:true,
  trains:[{...train(),state:'running',direction:0},{...train(),id:'B',state:'braking',direction:1},{...train(),id:'C',state:'dwelling',direction:1}]};
 const before=JSON.stringify(state),frame=toMotionFrame(state);
 assert.equal(frame.epoch,'era-run');assert.equal(frame.sim_time_s,5.2);assert.equal(frame.speed,15);
 assert.deepEqual(frame.trains.map(t=>t.direction),[1,1,1]);
 assert.deepEqual(frame.trains.map(t=>t.status),['moving','moving','dwelling']);
 assert.equal(JSON.stringify(state),before);
});

test('actual compact map decoding agrees with its source IDs and explicitly marks precision loss',()=>{
 const text=readFileSync(new URL('../../map_data.js',import.meta.url),'utf8');
 const compact=JSON.parse(text.slice('window.RAIL='.length).trim().replace(/;$/,''));
 const exact=JSON.parse(readFileSync(new URL('../../network.json',import.meta.url),'utf8'));
 const decoded=decodeCompactRail(compact);
 assert.equal(decoded.precision,'compact');assert.equal(decoded.edges.length,exact.edges.length);
 for(const id of [0,75,Math.floor(exact.edges.length/2),exact.edges.length-1]){
  const a=decoded.edges[id],b=exact.edges[id];assert.equal(a.u,b.u);assert.equal(a.v,b.v);assert.equal(a.way,b.way);
  assert.equal(a.category,b.category);assert.equal(a.length_m,Math.round(b.length_m));
  for(let i=0;i<a.geometry.length;i++){close(a.geometry[i][0],b.geometry[i][0],.0000051);close(a.geometry[i][1],b.geometry[i][1],.0000051);}
 }
 assert.equal(decoded.edges.filter(e=>e.synthetic).length,exact.edges.filter(e=>exact.ways[e.way].synthetic).length);
 const upgraded=decodeCompactRail(compact,exact);assert.equal(upgraded.precision,'exact');
 assert.equal(upgraded.edges[0].length_m,exact.edges[0].length_m);
 assert.equal(upgraded.stations.length,exact.stations.length);
});
