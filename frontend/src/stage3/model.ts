import type {DispatchReport,Movement} from '../types';

export interface DemoTrain {id:string;number:string;direction:number;type:'passenger'|'freight';priority:number;route:number[]}
export interface DemoData {
 trains:DemoTrain[];length_m:number;
 stations:{id:string;name:string;position_m:number}[];
 sections:{id:string;from_station:string;to_station:string;length_m:number}[];
 before:Pick<DispatchReport,'valid'|'conflicts'|'decisions'|'section_order'|'policy'>;
 after?:DemoData['before']|null;
 profiles?:Record<string,[number,number][]>;
 calculation_s?:number;solver_status?:string;status?:string;
}

export const timeLabel=(seconds:number)=>{
 const t=Math.max(0,Math.floor(seconds));
 return `${String(8+Math.floor(t/3600)).padStart(2,'0')}:${String(Math.floor(t%3600/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')}`;
};

export function movementAt(moves:Movement[],time:number) {
 return moves.find(m=>time<m.end_s)||moves.at(-1);
}

export function trainPosition(data:DemoData,train:DemoTrain,time:number):{position:number;status:string} {
 const legs=(data.after?.decisions||[]).filter(m=>m.train_id===train.id).sort((a,b)=>a.leg-b.leg);
 let position=train.direction===1?0:data.length_m;
 for(const m of legs){
  const section=data.sections.find(s=>s.id===m.section_id)!;
  const origin=data.stations.find(s=>s.id===(train.direction===1?section.from_station:section.to_station))!.position_m;
  if(time<m.start_s)return {position:origin,status:'Ожидает'};
  if(time<m.end_s){
   const points=data.profiles?.[`${train.id}:${m.leg}`];
   let distance=section.length_m*(time-m.start_s)/(m.end_s-m.start_s);
   if(points){
    const elapsed=time-m.start_s;
    const index=points.findIndex(p=>p[0]>=elapsed);
    const b=points[Math.max(0,index)],a=points[Math.max(0,index-1)];
    distance=a[1]+(b[1]-a[1])*(b[0]===a[0]?0:(elapsed-a[0])/(b[0]-a[0]));
   }
   return {position:origin+train.direction*distance,status:'В движении'};
  }
  position=origin+train.direction*section.length_m;
 }
 return {position,status:legs.length?'Прибыл':'Ожидает расчёта'};
}
