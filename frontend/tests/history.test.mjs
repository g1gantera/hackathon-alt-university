import {test} from 'node:test';
import assert from 'node:assert/strict';
import {matchesFrame,eventFrame,reportUrl} from '../src/history.ts';

const frames=[
 {id:10,sim_time_s:300,state_version:301},
 {id:12,sim_time_s:300,state_version:302},
 {id:14,sim_time_s:300,state_version:304},
 {id:16,sim_time_s:600,state_version:604},
];
test('archive rejects late responses from a different reset or a paused state at the same time',()=>{
 const snapshot={epoch:'run1',sim_time_s:300,state_version:302};
 assert.equal(matchesFrame(snapshot,'run1',frames[1]),true);
 assert.equal(matchesFrame(snapshot,'run2',frames[1]),false);
 assert.equal(matchesFrame(snapshot,'run1',frames[0]),false);
 assert.equal(matchesFrame({...snapshot,sim_time_s:301},'run1',frames[1]),false);
});
test('event selection chooses the first snapshot containing that change, including paused events',()=>{
 assert.equal(eventFrame(frames,{state_version:302,sim_time_s:300}),1);
 assert.equal(eventFrame(frames,{state_version:303,sim_time_s:300}),2);
 assert.equal(eventFrame(frames,{state_version:301,sim_time_s:300}),0);
 assert.equal(eventFrame(frames,{state_version:700,sim_time_s:700}),3);
});
test('download is anchored to the selected archive window, not to the advancing live clock',()=>{
 const window={epoch:'old/run',minutes:10,to_s:600,through_id:16};
 const url=new URL(reportUrl(window),'http://localhost');
 assert.equal(url.pathname,'/api/reports/history.csv');
 assert.deepEqual(Object.fromEntries(url.searchParams),{epoch:'old/run',minutes:'10',to:'600',through_id:'16'});
});
