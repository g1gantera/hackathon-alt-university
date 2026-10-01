import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import {runInNewContext} from 'node:vm';
import {transformSync} from 'esbuild';

test('the served 3D graph decompresses to the exact era graph with matching provenance',()=>{
 const source=readFileSync(new URL('../../network.json',import.meta.url));
 const served=gunzipSync(readFileSync(new URL('../../static/scene3d/network.json.gz',import.meta.url)));
 const provenance=JSON.parse(readFileSync(new URL('../../static/scene3d/network-source.json',import.meta.url),'utf8'));
 assert.deepEqual(served,source,'The 3D graph must retain every source coordinate, length and ID');
 assert.equal(provenance.source,'network.json');
 assert.equal(provenance.bytes,source.length);
 assert.equal(provenance.sha256,createHash('sha256').update(source).digest('hex'));
});

// Exercise the actual public message boundary before WebGL initialization.
// The harness exposes its closures only inside this VM; no production export,
// renderer stub or alternate message-handling implementation is introduced.
const source=readFileSync(new URL('../scene3d-src/main.ts',import.meta.url),'utf8');
const boundary=source.indexOf('async function main(){');
assert.ok(boundary>0,'The standalone viewer initialization boundary must exist');
const bridgeSource=transformSync(source.slice(0,boundary)+`
globalThis.bridge={
 get pending(){return pending;},
 onState(callback){receive=callback;},
 onFocus(callback){focus=callback;},
 onLayers(callback){toggleLayers=callback;},
 post
};`,{loader:'ts',format:'cjs',target:'es2022'}).code;

function harness(){
 const handlers=[],sent=[],origin='https://railway.example';
 const parent={postMessage:(message,target)=>sent.push({message,target})};
 const window={addEventListener:(event,handler)=>{assert.equal(event,'message');handlers.push(handler);}};
 const forbidden=()=>assert.fail('The message bridge must not call a network or simulation API');
 const context={parent,window,location:{origin},fetch:forbidden,XMLHttpRequest:forbidden,WebSocket:forbidden,
  api:forbidden,action:forbidden,document:{getElementById:forbidden}};
 runInNewContext(bridgeSource,context);
 return {bridge:context.bridge,parent,origin,sent,
  message:(data,overrides={})=>handlers.forEach(handler=>handler({data,origin,source:parent,...overrides}))};
}

test('the renderer accepts read-only state only from its same-origin parent',()=>{
 const h=harness(),received=[];
 const state=Object.freeze({run_id:'run-1',version:1,trains:Object.freeze([])});
 const message=Object.freeze({type:'state',state,selected:null,replay:false});
 h.bridge.onState(value=>received.push(value));
 h.message(message,{origin:'https://unrelated.example'});
 h.message(message,{source:{}});
 h.message(null);
 h.message({type:'dispatch',state});
 assert.equal(h.bridge.pending,null);assert.equal(received.length,0);
 h.message(message);
 assert.equal(h.bridge.pending,message);assert.equal(received[0],message);
 assert.equal(state.version,1);assert.deepEqual(state.trains,[]);
 assert.equal(h.sent.length,0,'Receiving state must not acknowledge or mutate it before rendering');
});

test('loading retains the newest parent frame and presentation commands never become dispatch actions',()=>{
 const h=harness();let focuses=0,layers=0;
 const first={type:'state',state:{run_id:'run-1',version:1},selected:null,replay:false};
 const latest={type:'state',state:{run_id:'run-1',version:2},selected:'T-2',replay:false};
 h.message(first);h.message(latest);
 assert.equal(h.bridge.pending,latest);
 h.bridge.onFocus(()=>focuses++);h.bridge.onLayers(()=>layers++);
 h.message({type:'focus'},{origin:'https://unrelated.example'});
 h.message({type:'layers'},{source:{}});
 h.message({type:'run'});h.message({type:'pause'});h.message({type:'reroute'});
 assert.equal(focuses,0);assert.equal(layers,0);
 h.message({type:'focus'});h.message({type:'layers'});
 assert.equal(focuses,1);assert.equal(layers,1);assert.equal(h.bridge.pending,latest);
 h.bridge.post({type:'map-rendered',version:2});
 assert.equal(h.sent.length,1);assert.equal(h.sent[0].target,h.origin);
 assert.deepEqual(h.sent[0].message,{type:'map-rendered',version:2});
});
