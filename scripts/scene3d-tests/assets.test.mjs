import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { Box3, Mesh, Vector3 } from 'three';
import { createArticulatedTrain, createTrain, objectStats, disposeObject } from '../scene3d-src/assets.ts';

const near = (actual, expected, tolerance = 0.0001) => {
  assert.ok(Math.abs(actual - expected) < tolerance, `${actual} differs from ${expected}`);
};
const meshes = object => {
  const result = [];
  object.traverse(child => { if (child instanceof Mesh) result.push(child); });
  return result;
};

test('extracted models are byte-identical to the M_part asset source', () => {
  const bytes = readFileSync(new URL('../scene3d-src/assets.ts', import.meta.url));
  const hash = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  assert.equal(hash, '7da14bf4b9b660ed61cf713ecdf6ee9cff15cfbe');
});

for (const type of ['passenger', 'freight']) {
  test(`${type} articulation preserves the complete train envelope and meter-scale pivots`, () => {
    const train = createArticulatedTrain(type, 4);
    const rigid = createTrain(type, 4);
    const bounds = new Box3().setFromObject(train);
    const rigidBounds = new Box3().setFromObject(rigid);
    for (const limit of ['min', 'max']) for (const axis of ['x', 'y', 'z']) near(bounds[limit][axis], rigidBounds[limit][axis]);
    assert.equal(train.userData.articulated, true);
    assert.equal(train.userData.vehicles.length, 5);
    near(train.userData.frontOffset, bounds.max.z);
    near(train.userData.rearOffset, -bounds.min.z);
    near(train.userData.length, bounds.max.z - bounds.min.z);
    const sum = new Box3();
    train.userData.vehicles.forEach((vehicle, index) => {
      const expectedOffset = index === 0 ? 0 : 21.2 + (index - 1) * 23.4;
      near(vehicle.centerOffset, expectedOffset);
      near(vehicle.object.position.z, -expectedOffset);
      near(vehicle.wheelbase, index === 0 ? 11.52 : 14.08);
      assert.equal(vehicle.object.parent, train);
      assert.equal(vehicle.bogies.length, 2);
      near(vehicle.bogies[0].offset, vehicle.wheelbase / 2);
      near(vehicle.bogies[1].offset, -vehicle.wheelbase / 2);
      assert.ok(vehicle.frontOffset > 9 && vehicle.rearOffset > 9);
      for (const bogie of vehicle.bogies) {
        assert.equal(bogie.object.parent, vehicle.object);
        near(bogie.object.position.z, bogie.offset);
        assert.equal(objectStats(bogie.object).meshes, 1);
      }
      sum.union(new Box3().setFromObject(vehicle.object));
    });
    near(sum.min.z, bounds.min.z);
    near(sum.max.z, bounds.max.z);
    assert.equal(objectStats(train).triangles, objectStats(rigid).triangles);
    assert.ok(objectStats(train).meshes <= train.userData.vehicles.length * 7);
    assert.ok(meshes(train).every(mesh => mesh.geometry.getAttribute('position').array.every(Number.isFinite)));
  });
}

test('fresh consists share per-car geometry without sharing any mutable vehicle or bogie transforms', () => {
  const first = createArticulatedTrain('passenger');
  const second = createArticulatedTrain('passenger');
  const firstMeshes = meshes(first), secondMeshes = meshes(second);
  assert.equal(firstMeshes.length, secondMeshes.length);
  firstMeshes.forEach((mesh, index) => {
    assert.notEqual(mesh, secondMeshes[index]);
    assert.equal(mesh.geometry, secondMeshes[index].geometry);
    assert.equal(mesh.material, secondMeshes[index].material);
    assert.equal(mesh.geometry.userData.sharedRailAsset, true);
  });
  const front = first.userData.vehicles[0], coach = first.userData.vehicles[1];
  const secondCoach = second.userData.vehicles[1];
  assert.notEqual(coach.object, secondCoach.object);
  assert.notEqual(coach.bogies[0].object, secondCoach.bogies[0].object);
  const retainedGeometry = coach.object.children[0].geometry;
  const frontPosition = front.object.position.clone();
  coach.object.position.set(5, 0, -20);
  coach.object.rotation.y = Math.PI / 3;
  coach.bogies[0].object.rotation.y = -0.13;
  coach.bogies[1].object.rotation.y = 0.13;
  first.updateMatrixWorld(true);
  assert.deepEqual(front.object.position, frontPosition);
  near(secondCoach.object.position.x, 0);
  near(secondCoach.object.rotation.y, 0);
  near(secondCoach.bogies[0].object.rotation.y, 0);
  assert.equal(coach.object.children[0].geometry, retainedGeometry);
  const frontPivot = coach.bogies[0].object.getWorldPosition(new Vector3());
  const rearPivot = coach.bogies[1].object.getWorldPosition(new Vector3());
  near(frontPivot.distanceTo(rearPivot), coach.wheelbase);
  near((frontPivot.x + rearPivot.x) / 2, coach.object.position.x);
  near((frontPivot.z + rearPivot.z) / 2, coach.object.position.z);
});

test('identical coaches and repeated freight liveries reuse their body template', () => {
  const passenger = createArticulatedTrain('passenger', 5).userData.vehicles;
  assert.equal(passenger[1].object.children[0].geometry, passenger[5].object.children[0].geometry);
  const freight = createArticulatedTrain('freight', 5).userData.vehicles;
  assert.equal(freight[1].object.children[0].geometry, freight[4].object.children[0].geometry);
  assert.equal(freight[2].object.children[0].geometry, freight[5].object.children[0].geometry);
  assert.equal(freight[0].bogies[0].object.children[0].geometry, freight[5].bogies[1].object.children[0].geometry);
});

test('empty and maximum-size consists retain valid metadata and shared resources survive disposal', () => {
  for (const [requested, expected] of [[-1, 0], [NaN, 0], [Infinity, 0], [100, 12], [2.6, 3]]) {
    const train = createArticulatedTrain('freight', requested);
    assert.equal(train.userData.carCount, expected);
    assert.equal(train.userData.vehicles.length, expected + 1);
    assert.ok(Number.isFinite(train.userData.length));
  }
  const train = createArticulatedTrain('passenger', 0);
  const geometry = train.userData.vehicles[0].bogies[0].object.children[0].geometry;
  let disposed = false;
  const onDispose = () => { disposed = true; };
  geometry.addEventListener('dispose', onDispose);
  disposeObject(train);
  geometry.removeEventListener('dispose', onDispose);
  assert.equal(disposed, false);
  assert.equal(createArticulatedTrain('passenger', 0).userData.vehicles[0].bogies[0].object.children[0].geometry, geometry);
});

test('wheel treads align with the scene’s 1.52 metre track gauge',()=>{
 const train=createArticulatedTrain('passenger',1);
 for(const vehicle of train.userData.vehicles)for(const bogie of vehicle.bogies){
  const positions=bogie.object.children[0].geometry.getAttribute('position');
  const wheels=[];
  // Below the bolster (y=.435) every vertex belongs to one of the four wheels.
  for(let i=0;i<positions.count;i++)if(positions.getY(i)<.4)wheels.push(positions.getX(i));
  const left=wheels.filter(x=>x<0),right=wheels.filter(x=>x>0);
  assert.ok(left.length&&right.length);
  assert.ok(Math.abs((Math.min(...left)+Math.max(...left))/2+.76)<1e-5);
  assert.ok(Math.abs((Math.min(...right)+Math.max(...right))/2-.76)<1e-5);
 }
});

import {TrainMotionBuffer} from '../scene3d-src/motion.ts';

const close=(actual,expected,tolerance=1e-8)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} differs from ${expected}`);
const snapshot=(time,position,overrides={},trainOverrides={})=>({
 epoch:'run-a',sim_time_s:time,state_version:time,running:true,speed:30,
 trains:[{id:'T01',position_m:position,direction:1,status:'moving',...trainOverrides}],
 ...overrides,
});
const position=(motion,now)=>motion.sample('T01',now).position;

test('motion starts on the authoritative point and never extrapolates through a packet gap',()=>{
 const motion=new TrainMotionBuffer();
 assert.equal(motion.sample('missing',0),undefined);
 assert.equal(motion.isAnimating(0),false);
 motion.push(snapshot(0,100),0);
 assert.deepEqual(motion.sample('T01',0),{position:100,animating:false});
 motion.push(snapshot(1,200),500);
 close(position(motion,500),100);
 assert.equal(motion.isAnimating(700),true);
 let previous=100;
 for(let now=500;now<=2000;now+=10){
  const point=position(motion,now);
  assert.ok(point>=previous-1e-9&&point<=200);
  previous=point;
 }
 assert.deepEqual(motion.sample('T01',1151),{position:200,animating:false});
 assert.deepEqual(motion.sample('T01',100000),{position:200,animating:false});
});

test('jittering packet cadence retargets from the displayed point without backwards jumps',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0),0);
 let clock=0,last=0,target=0;
 for(const [index,arrival] of [430,890,1210,1780,2260,2640,3250].entries()){
  for(;clock<arrival;clock+=10){
   const point=position(motion,clock);
   assert.ok(point>=last-1e-8&&point<=target+1e-8);
   last=point;
  }
  const displayed=position(motion,arrival);
  target=(index+1)*30;
  motion.push(snapshot(index+1,target),arrival);
  close(position(motion,arrival),displayed);
  last=displayed;clock=arrival;
 }
 close(position(motion,3901),target);
 assert.equal(motion.isAnimating(3901),false);
});

test('retargeting preserves velocity when a bounded monotone continuation is feasible',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0),0);
 motion.push(snapshot(1,100),500);
 const now=850,epsilon=.01;
 const before=(position(motion,now)-position(motion,now-epsilon))/epsilon;
 motion.push(snapshot(2,200),now);
 const after=(position(motion,now+epsilon)-position(motion,now))/epsilon;
 assert.ok(before>0);
 close(before,after,.00002);
});

test('selection rerenders and same-time heartbeats do not restart or extend the tween',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0),0);
 const observed=snapshot(1,100);
 motion.push(observed,500);
 for(let now=516;now<=1204;now+=16){
  const displayed=position(motion,now);
  // Same object, as when selecting another train; new object, as with a heartbeat.
  motion.push(observed,now);
  motion.push({...observed,state_version:now},now);
  close(position(motion,now),displayed);
 }
 close(position(motion,1204),100);
 assert.equal(motion.isAnimating(1204),false);
});

test('1x whole-second clock keeps moving between repeated 2 Hz telemetry frames',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0,{speed:1}),0);
 motion.push(snapshot(0,0,{speed:1}),500);
 motion.push(snapshot(1,20,{speed:1}),1000);
 const halfway=position(motion,1500);
 motion.push(snapshot(1,20,{speed:1}),1500);
 close(position(motion,1500),halfway);
 assert.ok(position(motion,1800)>halfway);
 assert.ok(position(motion,1990)>position(motion,1900));
 assert.equal(motion.isAnimating(1999),true);
 const displayed=position(motion,2000);
 motion.push(snapshot(2,40,{speed:1}),2000);
 close(position(motion,2000),displayed);
 assert.ok(position(motion,2100)>displayed);
 assert.deepEqual(motion.sample('T01',3201),{position:40,animating:false});
});

test('a 1x pause shortens a pending long-cadence tween to a 500 ms settle',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0,{speed:1}),0);
 motion.push(snapshot(1,20,{speed:1}),1000);
 const displayed=position(motion,1050);
 motion.push(snapshot(1,20,{speed:1,running:false}),1050);
 close(position(motion,1050),displayed);
 assert.ok(position(motion,1200)<20);
 assert.deepEqual(motion.sample('T01',1551),{position:20,animating:false});
});

test('pause settles in at most 500 ms, repeated paused snapshots freeze, resume is continuous',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,0),0);
 motion.push(snapshot(1,100),500);
 const displayed=position(motion,510);
 motion.push(snapshot(1,100,{running:false}),510);
 close(position(motion,510),displayed);
 for(let now=550;now<=1050;now+=50)motion.push(snapshot(1,100,{running:false,state_version:now}),now);
 assert.deepEqual(motion.sample('T01',1051),{position:100,animating:false});
 motion.push(snapshot(1,100),1500);
 assert.deepEqual(motion.sample('T01',1500),{position:100,animating:false});
 motion.push(snapshot(2,120),2000);
 close(position(motion,2000),100);
 assert.ok(position(motion,2250)>100&&position(motion,2250)<120);
 close(position(motion,2651),120);
});

test('station arrival and stopped observations settle smoothly instead of jumping to a packet target',()=>{
 for(const arrival of [snapshot(2,125,{}, {status:'waiting'}),snapshot(2,125,{}, {status:'stopped'})]){
  const motion=new TrainMotionBuffer();
  motion.push(snapshot(0,0),0);motion.push(snapshot(1,100),500);
  const displayed=position(motion,900);
  motion.push(arrival,900);
  close(position(motion,900),displayed);
  assert.ok(position(motion,1000)>displayed&&position(motion,1000)<125);
  close(position(motion,1401),125);
  assert.equal(motion.isAnimating(1401),false);
 }
});

test('reverse-running trains interpolate monotonically and a direction change snaps explicitly',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,1000,{}, {direction:-1}),0);
 motion.push(snapshot(1,900,{}, {direction:-1}),500);
 let previous=1000;
 for(let now=500;now<1200;now+=10){
  const point=position(motion,now);
  assert.ok(point<=previous+1e-9&&point>=900);
  previous=point;
 }
 motion.push(snapshot(2,920,{}, {direction:1}),1300);
 assert.deepEqual(motion.sample('T01',1300),{position:920,animating:false});
 motion.push(snapshot(3,950),1800);
 close(position(motion,1800),920);
 assert.ok(position(motion,2000)>920&&position(motion,2000)<950);
});

test('reset, rewind, server restart and large model-time jump discard the old trajectory',()=>{
 const cases=[
  snapshot(0,5000,{epoch:'run-b'}),
  snapshot(0,5000),
  snapshot(601,5000),
  snapshot(2,5000,{stream_id:'new-server'}),
 ];
 for(const next of cases){
  const motion=new TrainMotionBuffer();
  motion.push(snapshot(0,0,{stream_id:'old-server'}),0);
  motion.push(snapshot(1,100,{stream_id:'old-server'}),500);
  assert.equal(motion.isAnimating(750),true);
  motion.push(next,750);
  assert.deepEqual(motion.sample('T01',750),{position:5000,animating:false});
 }
});

test('removed trains release their animation state and malformed times cannot poison it',()=>{
 const motion=new TrainMotionBuffer();
 motion.push(snapshot(0,10),0);
 motion.push(snapshot(NaN,900),500);
 motion.push(snapshot(1,900),NaN);
 close(position(motion,500),10);
 motion.push(snapshot(1,10,{trains:[]}),600);
 assert.equal(motion.sample('T01',600),undefined);
 assert.equal(motion.isAnimating(600),false);
});
