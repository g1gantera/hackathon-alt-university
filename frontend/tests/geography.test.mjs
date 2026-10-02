import {test} from 'node:test';
import assert from 'node:assert/strict';
import {geographicRoute,mercator,modelMatrix,trackYaw} from '../src/rail3d/geography.ts';

const topology={length_m:3000,stations:[
 {id:'a',position_m:0,coordinate:[70,52]},
 {id:'b',position_m:1000,coordinate:[70.01,52.01]},
 {id:'c',position_m:3000,coordinate:[70.02,52.02]},
],sections:[
 {from_station:'a',to_station:'b',length_m:1000,geometry:[[70,52],[70,52],[70.01,52],[70.01,52.01]]},
 {from_station:'b',to_station:'c',length_m:2000,geometry:[[70.01,52.01],[70.02,52.02]]},
]};
const near=(a,b,eps=1e-9)=>assert.ok(Math.abs(a-b)<eps,`${a} != ${b}`);

test('chainage hits every station despite different section lengths and duplicate points',()=>{
 const route=geographicRoute(topology);
 for(const station of topology.stations){
  const p=route.point(route.distance(station.position_m));
  p.forEach((v,i)=>near(v,station.coordinate[i]));
 }
});

test('positions follow the rail bend, not a straight station-to-station shortcut',()=>{
 const route=geographicRoute(topology);
 const early=route.point(route.distance(100)),late=route.point(route.distance(900));
 near(early[1],52);near(late[0],70.01);
 assert.ok(early[0]>70&&early[0]<70.01);
 assert.ok(late[1]>52&&late[1]<52.01);
});

test('haversine-weighted position matches the backend locate contract',()=>{
 // Independent expected distance: 0.01 degree east at 52 degrees, then 0.01 north.
 const east=2*6371000*Math.asin(Math.cos(52*Math.PI/180)*Math.sin(.005*Math.PI/180));
 const north=6371000*.01*Math.PI/180;
 const ratio=(.5*(east+north)-east)/north;
 const p=geographicRoute(topology).point(geographicRoute(topology).distance(500));
 near(p[0],70.01);near(p[1],52+.01*ratio);
});

test('reverse consists trail toward larger chainage and terminal cars do not stack',()=>{
 const route=geographicRoute(topology),head=route.distance(2000);
 assert.ok(route.point(head-100)[1]<route.point(head)[1]);
 assert.ok(route.point(head+100)[1]>route.point(head)[1]);
 assert.ok(route.point(-100)[0]<route.point(0)[0]);
 assert.ok(route.point(route.length+100)[1]>route.point(route.length)[1]);
 const forward=trackYaw([70,52],[70,53]),reverse=trackYaw([70,52],[70,53],-1);
 near(Math.cos(forward-reverse),-1);
});

test('Mercator axes place model height above map and its front in travel direction',()=>{
 const identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
 const coordinate=[70,52],m=mercator(coordinate),matrix=modelMatrix(identity,coordinate,Math.PI);
 near(matrix[12],m.x,1e-7);near(matrix[13],m.y,1e-7);
 assert.ok(matrix[6]>0,'model Y becomes map altitude');
 assert.ok(matrix[9]<0,'northbound model Z points north in Mercator');
});

test('camera-relative matrix retains centimetre offsets at high zoom far from world origin',()=>{
 const coordinate=[70.3,53.2],m=mercator(coordinate),zoomScale=1e8;
 const projection=[zoomScale,0,0,0,0,zoomScale,0,0,0,0,zoomScale,0,-m.x*zoomScale+.025,-m.y*zoomScale+.05,0,1];
 const matrix=modelMatrix(projection,coordinate,0);
 near(matrix[12],.025,1e-7);near(matrix[13],.05,1e-7);
});

test('rounded station chainages do not push the head beyond the final rail coordinate',()=>{
 const route=geographicRoute({...topology,length_m:3000.003});
 assert.deepEqual(route.point(route.distance(3000.003)),topology.stations.at(-1).coordinate);
 assert.deepEqual(route.point(route.distance(-1)),topology.stations[0].coordinate);
});
