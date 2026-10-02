import type {Snapshot,Topology,Train} from '../../frontend/src/types';
export function trainNotice(train:Train,forecastValid?:boolean):{text:string;level:string};
export function incidentNotices(snapshot:Snapshot,topology:Topology):{id:string;coordinate?:[number,number];train_id?:string;kind:string;text:string;end_s:number;target:string}[];
