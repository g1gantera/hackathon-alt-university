import type {Snapshot} from './types';
import {receivedSnapshot} from './performanceProbe.ts';

// REST can finish after a newer socket update, including a simulation reset.
export function acceptSnapshot(current:Snapshot|null,next:Snapshot,fromSocket=false):boolean {
 const incoming=next.realtime,previous=current?.realtime;
 if(!incoming)return !previous;
 if(!previous)return true;
 if(incoming.stream_id!==previous.stream_id)return fromSocket;
 return incoming.seq>=previous.seq;
}

// Each new frame must clear stale state immediately. At 2 Hz a one-second
// interval recreated for every frame would otherwise never get a turn to run.
export function watchFreshness(lastUpdate:number,notify:(stale:boolean)=>void):()=>void {
 const check=()=>notify(Date.now()-lastUpdate>5000);
 check();
 const timer=setInterval(check,1000);
 return()=>clearInterval(timer);
}

interface Callbacks {
 snapshot:(snapshot:Snapshot)=>void;
 connected:(value:boolean)=>void;
 event:(type:string,payload:any)=>void;
 ready:()=>void;
 unauthorized:()=>void;
}

// Every connection starts with a full snapshot; no accumulated position deltas.
export function connectRealtime(callbacks:Callbacks):()=>void {
 let stopped=false,socket:WebSocket|null=null,retry:ReturnType<typeof setTimeout>|undefined;
 let attempts=0,lastFrame=Date.now();
 const connect=()=>{
  if(stopped)return;
  callbacks.connected(false);
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);
  socket=ws;lastFrame=Date.now();
  let sequence:number|null=null,stream:string|null=null;
  ws.onmessage=event=>{
   const received=performance.now();
   if(stopped||socket!==ws)return;
   try {
    const data=JSON.parse(event.data);
    if(data.protocol_version!==1||!Number.isSafeInteger(data.seq)||typeof data.stream_id!=='string')throw new Error('Invalid stream');
    if(sequence!==null&&data.stream_id===stream&&data.seq<=sequence)return;
    if(sequence===null&&data.type!=='state.updated')throw new Error('Missing initial snapshot');
    if(sequence!==null&&(data.stream_id!==stream||data.seq!==sequence+1))throw new Error('Stream gap');
    if(data.type==='state.updated'){
     const state=data.payload as Snapshot;
     if(!Array.isArray(state.trains)||state.realtime?.seq!==data.seq||state.realtime?.stream_id!==data.stream_id)throw new Error('Invalid snapshot');
     receivedSnapshot(state,received);
     callbacks.snapshot(state);
     callbacks.connected(true);
     if(sequence===null)callbacks.ready();
     attempts=0;
    }
    sequence=data.seq;stream=data.stream_id;lastFrame=Date.now();
    callbacks.event(data.type,data.payload);
   }catch {ws.close();}
  };
  ws.onerror=()=>ws.close();
  ws.onclose=event=>{
   if(stopped||socket!==ws)return;
   callbacks.connected(false);
   if(event.code===1008){callbacks.unauthorized();return;}
   retry=setTimeout(connect,Math.min(1000*2**attempts++,10000));
  };
 };
 const watchdog=setInterval(()=>{
  if(socket&&socket.readyState<2&&Date.now()-lastFrame>5000){callbacks.connected(false);socket.close();}
 },1000);
 connect();
 return()=>{stopped=true;clearTimeout(retry);clearInterval(watchdog);socket?.close();callbacks.connected(false);};
}
