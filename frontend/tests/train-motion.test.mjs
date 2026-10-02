import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createTrainMotion} from '../src/trainMotion.ts';
import {geographicRoute} from '../src/rail3d/geography.ts';

const topology={length_m:2000,stations:[{id:'a',position_m:0},{id:'b',position_m:2000}],sections:[
 {from_station:'a',to_station:'b',length_m:2000,geometry:[[70,52],[70.01,52],[70.01,52.01]]},
]};
const route=geographicRoute(topology);
function snapshot(position,time=position,overrides={}){
 return {epoch:'run-1',running:true,sim_time_s:time,speed:1,realtime:{stream_id:'socket-1',update_interval_ms:500},
  trains:[{id:'one',position_m:position,coordinate:route.point(route.distance(position)),direction:1,status:'moving',bearing_deg:90,speed_mps:20}],...overrides};
}
const head=(motion,now)=>motion.sample(now).snapshot.trains[0];

test('both views can consume one sub-frame position along the rail bend',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(0),0);motion.accept(snapshot(1800),500);
 const frame=motion.sample(750),train=frame.snapshot.trains[0];
 assert.equal(train.position_m,900);assert.equal(frame.moving,true);
 assert.deepEqual(train.coordinate,route.point(route.distance(900)));
 assert.ok(train.coordinate[0]===70.01||train.coordinate[1]===52,'position stays on the L-shaped rails');
});

test('an early update retargets from the displayed position without a jump',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(0),0);motion.accept(snapshot(100),500);
 const before=head(motion,700).position_m;
 motion.accept(snapshot(200),700);
 assert.equal(head(motion,700).position_m,before);
 assert.ok(head(motion,950).position_m>before);assert.ok(head(motion,950).position_m<200);
});

test('missing updates never extrapolate past a confirmed position and idle frames stop',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(.123),0);motion.accept(snapshot(12.789),500);
 const target=motion.sample(1000);
 assert.equal(target.moving,false);assert.equal(target.snapshot.trains[0].position_m,12.789);
 assert.equal(head(motion,100000).position_m,12.789);
});

test('pause, clock hold, reset, backward seek, reconnect and long gaps snap immediately',()=>{
 for(const [change,at] of [
  [{running:false},750],[{clock_held_for_replan:true},750],[{epoch:'run-2'},750],
  [{sim_time_s:-1},750],[{realtime:{stream_id:'socket-2',update_interval_ms:500}},750],[{},4000],
 ]){
  const motion=createTrainMotion(topology);
  motion.accept(snapshot(0),0);motion.accept(snapshot(100),500);
  motion.accept(snapshot(180,180,change),at);
  assert.equal(head(motion,at).position_m,180);assert.equal(motion.sample(at).moving,false);
 }
});

test('history seeks and returning from a hidden view do not animate through unrelated positions',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(0),0);motion.accept(snapshot(100),500);
 motion.accept(snapshot(1500),750,true);
 assert.equal(head(motion,750).position_m,1500);
 motion.accept(snapshot(300),800);
 assert.equal(head(motion,800).position_m,300);
});

test('reverse trains interpolate backwards and face their direction',()=>{
 const reverse=(position,time)=>{const s=snapshot(position,time);s.trains[0].direction=-1;return s;};
 const motion=createTrainMotion(topology);
 motion.accept(reverse(600,0),0);motion.accept(reverse(400,1),500);
 assert.equal(head(motion,750).position_m,500);
 assert.ok(Math.abs(head(motion,750).bearing_deg-270)<1e-6);
});

test('repeated arrival frames finish at the station without restarting the animation',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(1800),0);
 const arrived=snapshot(2000);arrived.trains[0].status='waiting';
 motion.accept(arrived,500);motion.accept({...arrived,sim_time_s:2001},750);
 assert.equal(head(motion,750).position_m,1900);
 assert.equal(head(motion,1000).position_m,2000);assert.equal(motion.sample(1000).moving,false);
});

test('new/removed trains and changes of direction do not inherit another trajectory',()=>{
 const motion=createTrainMotion(topology);
 motion.accept(snapshot(0),0);motion.accept(snapshot(100),500);
 const changed=snapshot(200);changed.trains[0].direction=-1;
 motion.accept(changed,750);assert.equal(head(motion,750).position_m,200);
 const replacement=snapshot(1000);replacement.trains[0].id='two';
 motion.accept(replacement,800);assert.equal(head(motion,800).position_m,1000);
 assert.equal(motion.sample(800).snapshot.trains.length,1);
});

test('speed multiplier changes keep continuity and never mutate authoritative telemetry',()=>{
 const motion=createTrainMotion(topology),latest=snapshot(1000,30,{speed:100});
 motion.accept(snapshot(0),0);motion.accept(snapshot(10),500);
 const before=head(motion,700).position_m;
 motion.accept(latest,700);
 assert.equal(head(motion,700).position_m,before);
 assert.ok(head(motion,900).position_m<1000);
 assert.equal(latest.trains[0].position_m,1000);assert.equal(latest.trains[0].speed_mps,20);
});
