import type {Topology,Snapshot,Train} from '../../frontend/src/types';
export function poseAt(topology:Topology,before:Snapshot,after:Snapshot,train:Train,time:number):Train;
export function frameTime(before:Snapshot,after:Snapshot,received:number,now:number,duration:number,historical?:boolean):number;
