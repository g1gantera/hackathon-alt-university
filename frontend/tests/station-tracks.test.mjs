import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createStationTracks,YARD_HALF,TRACK_SPACING} from '../src/rail3d/stationTracks.ts';
import {railChunks,railMesh,RAIL_ALTITUDE} from '../src/rail3d/railGeometry.ts';
import {mercator} from '../src/rail3d/geography.ts';
import {followCenter} from '../src/cameraFollow.ts';

const topology={length_m:20000,stations:[
 {id:'a',position_m:0,coordinate:[70,52],tracks:4},
 {id:'b',position_m:20000,coordinate:[70.3,52],tracks:2},
],sections:[{from_station:'a',to_station:'b',length_m:20000,geometry:[[70,52],[70.3,52]]}]};
function train(id,position=0,direction=1){
 const layout=createStationTracks(topology);
 return {id,position_m:position,direction,length_m:600,coordinate:layout.route.point(layout.route.distance(position)),status:'waiting',station_id:'a'};
}
const snapshot=(trains,time=0,epoch='run')=>({trains,sim_time_s:time,epoch});
const separation=(a,b)=>{const p=mercator(a),q=mercator(b);return Math.hypot(p.x-q.x,p.y-q.y)/p.scale;};

test('all station trains get separate visible sidings in both directions without changing telemetry',()=>{
 const layout=createStationTracks(topology),original=snapshot([train('a'),train('b'),train('c',0,-1),train('d')]);
 const before=JSON.stringify(original),shown=layout.place(original);
 assert.equal(shown.trains.length,4);
 assert.equal(new Set(shown.trains.map(t=>t.displayTrack.index)).size,4);
 assert.ok(separation(shown.trains[0].coordinate,shown.trains[1].coordinate)>TRACK_SPACING-.01);
 assert.equal(JSON.stringify(original),before);
 for(const t of shown.trains)assert.deepEqual(t.coordinate,layout.point(layout.route.distance(t.position_m),t.displayTrack));
});

test('selection-independent assignments survive snapshot reordering and another train leaving',()=>{
 const layout=createStationTracks(topology);
 const first=layout.place(snapshot([train('a'),train('b'),train('c')]));
 const reordered=layout.place(snapshot([train('c'),train('b'),train('a',4000)],1));
 for(const id of ['b','c'])assert.deepEqual(reordered.trains.find(t=>t.id===id).displayTrack,first.trains.find(t=>t.id===id).displayTrack);
});

test('a departure keeps its siding until the whole consist clears the switches',()=>{
 const layout=createStationTracks(topology);
 layout.place(snapshot([train('a'),train('b')]));
 const chain=d=>d/layout.route.length*topology.length_m;
 const headOutside=layout.place(snapshot([train('b',chain(YARD_HALF+50))],1)).trains[0];
 assert.equal(headOutside.displayTrack.index,1);
 const tail=layout.route.distance(headOutside.position_m)-500;
 assert.ok(separation(layout.point(tail,headOutside.displayTrack),layout.route.point(tail))>1);
 const clear=layout.place(snapshot([train('b',chain(YARD_HALF+700))],2)).trains[0];
 assert.equal(clear.displayTrack,undefined);
});

test('sidings smoothly join the main line at both yard ends',()=>{
 const layout=createStationTracks(topology),track={stationId:'a',index:2};
 for(const sign of [-1,1]){
  const edge=sign*YARD_HALF;
  assert.deepEqual(layout.point(edge,track),layout.route.point(edge));
  assert.ok(separation(layout.point(edge-sign*.01,track),layout.route.point(edge-sign*.01))<.001);
 }
});

test('reset and archive rewind discard previous visual assignments',()=>{
 const layout=createStationTracks(topology);
 layout.place(snapshot([train('a'),train('b')],10));
 assert.equal(layout.place(snapshot([train('b')],11)).trains[0].displayTrack.index,1);
 assert.equal(layout.place(snapshot([train('b')],0,'new-run')).trains[0].displayTrack.index,0);
 layout.place(snapshot([train('a'),train('b')],20));
 assert.equal(layout.place(snapshot([train('a')],0)).trains[0].displayTrack.index,0);
});

test('capacity overflow remains visible without changing the declared station capacity',()=>{
 const layout=createStationTracks(topology);
 const shown=layout.place(snapshot(Array.from({length:6},(_,i)=>train(String(i)))));
 assert.equal(new Set(shown.trains.map(t=>t.displayTrack.index)).size,6);
 assert.equal(layout.trackCount(layout.yards[0]),6);
 assert.equal(topology.stations[0].tracks,4);
 assert.equal(layout.features().filter(f=>f.properties.station==='a').length,5);
});

test('rail chunks cover the main line, terminal stubs and every siding',()=>{
 const layout=createStationTracks(topology),chunks=railChunks(layout);
 assert.ok(chunks.some(c=>c.from<0));assert.ok(chunks.some(c=>c.to>layout.route.length));
 for(const yard of layout.yards)for(let i=1;i<yard.tracks;i++)assert.ok(chunks.some(c=>c.track?.stationId===yard.id&&c.track.index===i));
 const chunk=chunks.find(c=>c.track?.stationId==='a'&&c.track.index===1);
 const coarse=railMesh(chunk,layout,false),detail=railMesh(chunk,layout,true);
 assert.ok(coarse.triangles>0);assert.ok(detail.triangles>coarse.triangles);
 assert.ok([...detail.positions].every(Number.isFinite));
 assert.equal(Math.max(...detail.positions.filter((_,i)=>i%3===1)),0,'rail tops align with train rail-height origin');
 assert.ok(Math.min(...detail.positions.filter((_,i)=>i%3===1))+RAIL_ALTITUDE>0,'ballast remains above the map surface');
});

test('follow constrains only the center, preserving continuous wheel zoom and rotation',()=>{
 const target=[70,52],wheelProposal={center:[71,53],zoom:18.2,pitch:60,bearing:35};
 const constrained={...wheelProposal,center:followCenter(true,true,false,target)};
 assert.deepEqual(constrained,{...wheelProposal,center:target});
 assert.equal(followCenter(true,true,true,target),undefined,'focus flight can reach the newly selected train smoothly');
 assert.equal(followCenter(true,false,false,target),undefined,'manual pan releases the lock');
 assert.equal(followCenter(false,true,false,target),undefined,'schematic view does not move the hidden map');
});
