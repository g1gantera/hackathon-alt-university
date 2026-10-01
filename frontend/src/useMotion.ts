import {useEffect,useRef,useState} from 'react';
import {frameTime,poseAt} from '../../integration/static/motion.mjs';
import type {Snapshot,Topology} from './types';
export function useMotion(topology:Topology,snapshot:Snapshot,historical=false){
 const buffer=useRef({before:snapshot,after:snapshot,received:performance.now(),duration:500});
 const [trains,setTrains]=useState(snapshot.trains);
 useEffect(()=>{const now=performance.now(),prior=buffer.current;if(prior.after!==snapshot)buffer.current={before:prior.after,after:snapshot,received:now,duration:Math.max(16,now-prior.received)};},[snapshot]);
 useEffect(()=>{let frame=0;const draw=(now:number)=>{const b=buffer.current;const t=frameTime(b.before,b.after,b.received,now,b.duration,historical);setTrains(b.after.trains.map(train=>poseAt(topology,b.before,b.after,train,t)));frame=requestAnimationFrame(draw);};frame=requestAnimationFrame(draw);return()=>cancelAnimationFrame(frame);},[topology,historical]);
 return trains;
}
