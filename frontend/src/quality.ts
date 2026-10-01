import type {Metrics,QualityComponent,QualityResponse,QualityPoint,Snapshot} from './types.ts';

export const qualityNames:Record<QualityComponent,string>={schedule:'Точность отправлений',energy:'Энергоэффективность',capacity:'Использование участка',conflicts:'Отсутствие нарушений',arrival_accuracy:'Точность конечного прибытия',availability:'Доступность перегонов (v2)',plan_validity:'Допустимость расписания (v2)'};

export function qualityStatus(metrics:Metrics){
 const thresholds=metrics.formula?.thresholds??{normal:90,attention:70};
 const assessment=metrics.assessment??(metrics.index>=thresholds.normal?'on_track':metrics.index>=thresholds.attention?'attention':'disrupted');
 return {
  on_track:{label:'Норма',tone:'good',color:'green'},
  attention:{label:'Внимание',tone:'warning',color:'orange'},
  disrupted:{label:'Критично',tone:'poor',color:'red'},
 }[assessment];
}

export function qualityResponseMatches(data:QualityResponse|null,snapshot:Snapshot):boolean {
 return !!data&&data.epoch===snapshot.epoch&&data.active_plan_id===snapshot.active_plan_id&&data.constraint_version===snapshot.constraint_version&&data.actual.quality_signature===snapshot.metrics.quality_signature;
}

export function qualityTrend(data:QualityResponse|null,snapshot:Snapshot):QualityPoint[] {
 if(!qualityResponseMatches(data,snapshot))return [];
 const start=Math.max(0,snapshot.sim_time_s-900);
 const points=data!.trend.points.filter(p=>p.sim_time_s>=start&&p.sim_time_s<=snapshot.sim_time_s&&p.state_version<=snapshot.state_version);
 const point={sim_time_s:snapshot.sim_time_s,state_version:snapshot.state_version,index:snapshot.metrics.index};
 if(points.at(-1)?.state_version!==point.state_version)points.push(point);
 return points;
}

export function qualityDriver(metrics:Metrics):string {
 if((metrics.quality_version||1)<3)return 'Архивный индекс';
 const worst=metrics.drivers?.[0];
 return worst?qualityNames[worst.component]+' −'+worst.loss_points.toFixed(1)+' балла':'Отклонений не выявлено';
}
