import {test} from 'node:test';
import assert from 'node:assert/strict';
import {acceptSnapshot,connectRealtime,watchFreshness} from '../src/realtime.ts';

const snapshot=(seq,stream='server-1',epoch='run-1')=>({trains:[],epoch,realtime:{seq,stream_id:stream,protocol_version:1}});
test('fresh frames clear a stale login/reconnect immediately even at 2 Hz',t=>{
 t.mock.timers.enable({apis:['setInterval','Date']});
 const values=[];
 let stop=watchFreshness(Date.now()-6000,value=>values.push(value));
 assert.equal(values.at(-1),true);
 for(let frame=0;frame<12;frame++){
  t.mock.timers.tick(500);
  stop();
  stop=watchFreshness(Date.now(),value=>values.push(value));
  assert.equal(values.at(-1),false);
 }
 t.mock.timers.tick(6000);
 assert.equal(values.at(-1),true);
 stop();
 const count=values.length;
 t.mock.timers.tick(10000);
 assert.equal(values.length,count);
});
test('reject late REST and accept reset/new server only from socket',()=>{
 const current=snapshot(10);
 assert.equal(acceptSnapshot(current,snapshot(9)),false);
 assert.equal(acceptSnapshot(current,snapshot(11,'server-1','reset')),true);
 assert.equal(acceptSnapshot(current,snapshot(1,'server-2')),false);
 assert.equal(acceptSnapshot(current,snapshot(1,'server-2'),true),true);
 assert.equal(acceptSnapshot(current,{trains:[]}),false);
});

test('full initial state, duplicate suppression, gap recovery, watchdog and cleanup',t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date']});
 const sockets=[];
 class Socket {
  readyState=1;
  constructor(){sockets.push(this);}
  close(){this.readyState=3;this.onclose?.({code:1000});}
  send(seq,type='state.updated',stream='server-1'){
   this.onmessage({data:JSON.stringify({protocol_version:1,seq,stream_id:stream,type,payload:snapshot(seq,stream)})});
  }
 }
 globalThis.WebSocket=Socket;
 globalThis.location={protocol:'http:',host:'localhost'};
 const states=[],connections=[];
 let ready=0;
 const stop=connectRealtime({snapshot:s=>states.push(s),connected:s=>connections.push(s),event:()=>{},ready:()=>ready++,unauthorized:()=>{}});
 assert.equal(connections.at(-1),false);
 sockets[0].send(2);
 assert.equal(connections.at(-1),true);
 sockets[0].send(2);
 assert.equal(states.length,1);
 sockets[0].send(4);
 assert.equal(connections.at(-1),false);
 t.mock.timers.tick(1000);
 sockets[1].send(6);
 assert.equal(ready,2);
 sockets[0].send(100);
 assert.equal(states.at(-1).realtime.seq,6);
 t.mock.timers.tick(6000);
 assert.equal(sockets[1].readyState,3);
 t.mock.timers.tick(1000);
 sockets.at(-1).send(1,'state.updated','restarted');
 assert.equal(states.at(-1).realtime.stream_id,'restarted');
 stop();
 const count=sockets.length;
 t.mock.timers.tick(30000);
 assert.equal(sockets.length,count);
 delete globalThis.WebSocket;delete globalThis.location;
});
