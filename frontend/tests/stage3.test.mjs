import {test} from 'node:test';
import assert from 'node:assert/strict';
import {trainPosition,movementAt,timeLabel} from '../src/stage3/model.ts';

const train={id:'D01',direction:1};
const move={train_id:'D01',leg:0,section_id:'s',start_s:60,end_s:160,release_s:180};
const data={length_m:1000,stations:[{id:'a',position_m:0},{id:'b',position_m:1000}],sections:[{id:'s',from_station:'a',to_station:'b',length_m:1000}],after:{decisions:[move]},profiles:{'D01:0':[[0,0],[50,400],[100,1000]]}};

test('playback follows backend samples and waits at station',()=>{
 assert.deepEqual(trainPosition(data,train,30),{position:0,status:'Ожидает'});
 assert.equal(trainPosition(data,train,110).position,400);
 assert.deepEqual(trainPosition(data,train,160),{position:1000,status:'Прибыл'});
 assert.deepEqual(trainPosition(data,train,0),{position:0,status:'Ожидает'});
});
test('reverse train uses reverse route without changing sample distances',()=>{
 assert.equal(trainPosition(data,{...train,direction:-1},30).position,1000);
 assert.equal(trainPosition(data,{...train,direction:-1},110).position,600);
 assert.equal(trainPosition(data,{...train,direction:-1},160).position,0);
});
test('unplanned trains remain at origins and next leg changes at arrival',()=>{
 assert.equal(trainPosition({...data,after:null},train,500).position,0);
 assert.equal(trainPosition({...data,after:null},{...train,direction:-1},500).position,1000);
 const next={...move,leg:1,start_s:250,end_s:350};
 assert.equal(movementAt([move,next],160).leg,1);
 assert.equal(timeLabel(0),'08:00:00');
 assert.equal(timeLabel(3661),'09:01:01');
});
