import test from 'node:test';
import assert from 'node:assert/strict';
import {poseAt,frameTime} from '../static/motion.mjs';
const topology={stations:[{id:'a',position_m:0},{id:'b',position_m:100},{id:'c',position_m:200}]};
const moves=[{train_id:'t',section_id:'ab',origin:'a',destination:'b',start_s:0,end_s:10,main_track_id:'1'},{train_id:'t',section_id:'bc',origin:'b',destination:'c',start_s:20,end_s:30,main_track_id:'2'}];
const stops=[{train_id:'t',station_id:'a',track_id:'side',arrival_s:-10,departure_s:0},{train_id:'t',station_id:'b',track_id:'side',arrival_s:10,departure_s:20}];
const before={epoch:'e',active_plan_id:'p',sim_time_s:5,trains:[{id:'t',section_id:'ab',position_m:50}]};
const train={id:'t',section_id:'bc',position_m:150};
const after={epoch:'e',active_plan_id:'p',sim_time_s:25,trains:[train],plan:{movements:moves,stops}};
test('animation resolves intermediate station dwell and next lane instead of teleporting',()=>{
 for(let time=5;time<25;time+=.05){const p=poseAt(topology,before,after,train,time);assert.ok(p.position_m>=50&&p.position_m<=150);if(time>=10&&time<20){assert.equal(p.station_id,'b');assert.equal(p.station_track_id,'side');assert.equal(p.position_m,100);}}
 assert.equal(poseAt(topology,before,after,train,22).main_track_id,'2');
});
test('all simulation speeds and arbitrarily large steps interpolate; archive and new plans snap',()=>{
 for(const speed of [1,5,15,30,60,1000]){const end={...after,sim_time_s:before.sim_time_s+speed};assert.equal(frameTime(before,end,1000,1250,500),before.sim_time_s+speed/2);assert.equal(frameTime(before,end,1000,1250,500,true),end.sim_time_s);}
 assert.equal(frameTime(before,{...after,active_plan_id:'new'},1000,1100,500),25);
});

test('unified telemetry overrides an obsolete timetable while braking',()=>{
 const route={...topology,sections:[{id:'ab',from_station:'a',to_station:'b',length_m:100}]};
 const actual={id:'t',route:['a','b'],execution_frames:[
  {time_s:5,leg:0,x:20,speed_mps:4,moving:true,admitted:true,track:'side',arrival_track:'2',move:moves[0]},
  {time_s:6,leg:0,x:23,speed_mps:2,moving:true,admitted:true,track:'side',arrival_track:'2',move:moves[0]}]};
 const p=poseAt(route,before,{...after,sim_time_s:6},actual,5.5);
 assert.equal(p.position_m,21.5);assert.equal(p.speed_mps,3);
 assert.equal(p.main_track_id,'1');assert.equal(p.arrival_track_id,'2');
});
