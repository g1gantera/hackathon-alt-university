import {test} from 'node:test';
import assert from 'node:assert/strict';
import {qualityResponseMatches,qualityTrend,qualityDriver} from '../src/quality.ts';

const snapshot={epoch:'run1',active_plan_id:'plan1',constraint_version:0,sim_time_s:100,state_version:3,metrics:{index:96,quality_version:3,quality_signature:'f1',drivers:[{component:'capacity',loss_points:4}]}};
const data={epoch:'run1',active_plan_id:'plan1',constraint_version:0,actual:{quality_signature:'f1'},trend:{points:[{sim_time_s:0,state_version:0,index:100},{sim_time_s:100,state_version:2,index:100}]}};

test('quality response rejects other resets, timetables, constraints and formulas',()=>{
 assert.equal(qualityResponseMatches(data,snapshot),true);
 for(const changes of [{epoch:'run2'},{active_plan_id:'plan2'},{constraint_version:1},{metrics:{quality_signature:'f2'}}])assert.equal(qualityResponseMatches(data,{...snapshot,...changes}),false);
 assert.deepEqual(qualityTrend(data,{...snapshot,epoch:'run2'}),[]);
});
test('trend preserves incident changes at the same model time and excludes future/expired observations',()=>{
 assert.deepEqual(qualityTrend(data,snapshot).map(p=>p.index),[100,100,96]);
 const noisy={...data,trend:{points:[...data.trend.points,{sim_time_s:101,state_version:4,index:80}]}};
 assert.deepEqual(qualityTrend(noisy,snapshot).map(p=>p.index),[100,100,96]);
 const later={...snapshot,sim_time_s:1100,state_version:10};
 assert.deepEqual(qualityTrend(noisy,later),[{sim_time_s:1100,state_version:10,index:96}]);
});
test('driver labels actual losses and keeps legacy history labelled',()=>{
 assert.match(qualityDriver(snapshot.metrics),/4\.0/);
 assert.equal(qualityDriver({quality_version:2,index:80}),'Архивный индекс');
 assert.equal(qualityDriver({quality_version:3,index:100,drivers:[]}),'Отклонений не выявлено');
});
