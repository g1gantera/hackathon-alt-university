import test from 'node:test';
import assert from 'node:assert/strict';
import {trainNotice,incidentNotices} from '../static/map-alerts.mjs';
test('actual delay and predicted delay are distinct; invalid forecasts are hidden',()=>{
 assert.match(trainNotice({delay_s:90}).text,/Опоздание \+2/);
 assert.match(trainNotice({delay_s:0,eta_s:180,due_s:100}).text,/Прогноз/);
 assert.equal(trainNotice({delay_s:0,eta_s:180,due_s:100},false).text,'');
 assert.match(trainNotice({waiting_for:['P1']}).text,/Пропускает P1/);
 assert.equal(trainNotice({holding:true}).level,'danger');
});
test('incident markers resolve individual tracks and planned closures and expire',()=>{
 const topo={stations:[{id:'A',coordinate:[1,2]}],sections:[{id:'AB',geometry:[[1,2],[3,4]]}]};
 const state={sim_time_s:20,trains:[],incidents:[{id:'one',kind:'closure',target_id:'main_track:AB:2',start_s:30,end_s:100},{id:'expired',kind:'signal',target_id:'AB',start_s:0,end_s:20},{id:'station',kind:'closure',target_id:'track:A:SIM-1',start_s:0,end_s:60}]};
 const alerts=incidentNotices(state,topo);
 assert.equal(alerts.length,2);assert.match(alerts[0].text,/Предстоит/);
 assert.deepEqual(alerts[0].coordinate,[3,4]);assert.deepEqual(alerts[1].coordinate,[1,2]);
});
