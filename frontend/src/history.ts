import type {Snapshot} from './types';

export interface HistoryFrame {id:number;sim_time_s:number;saved_at:number;state_version:number;quality_index:number;plan_id:string}
export interface HistoryEvent {id:number;sim_time_s:number;state_version:number;type:string;kind:string|null;target_id:string|null;action:string|null}
export interface HistoryWindow {epoch:string;from_s:number;to_s:number;through_id:number;minutes:5|10|15;retention_hours:number;frames:HistoryFrame[];events:HistoryEvent[]}
export interface HistoryRun {epoch:string;first_time_s:number;last_time_s:number;saved_at:number;snapshot_count:number}

export function matchesFrame(snapshot:Snapshot,epoch:string,frame:HistoryFrame){
 return snapshot.epoch===epoch&&snapshot.state_version===frame.state_version&&snapshot.sim_time_s===frame.sim_time_s;
}
export function eventFrame(frames:HistoryFrame[],event:HistoryEvent){
 // Incident/replan events are persisted before the corresponding snapshot.
 const index=frames.findIndex(frame=>frame.state_version>=event.state_version);
 return index<0?Math.max(0,frames.length-1):index;
}
export function reportUrl(window:HistoryWindow){
 return '/api/reports/history.csv?'+new URLSearchParams({epoch:window.epoch,minutes:String(window.minutes),to:String(window.to_s),through_id:String(window.through_id)});
}
const labels:Record<string,string>={
 'incident.created':'Добавлен сбой','incident.resolved':'Сбой устранён','incident.expired':'Сбой завершился',
 'replan.queued':'Перерасчёт в очереди','replan.started':'Начат перерасчёт','replan.completed':'Варианты рассчитаны',
 'replan.failed':'Перерасчёт не завершён','plan.applied':'Применён план','eco.plan_ready':'Готов экономичный план',
 'settings.updated':'Изменены настройки','replan.options_changed':'Изменён режим диспетчера',
};
export function eventLabel(event:HistoryEvent){
 if(event.type==='simulation.changed')return ({start:'Запуск',pause:'Пауза',reset:'Новый запуск',speed:'Изменена скорость времени'} as Record<string,string>)[event.action||'']||'Управление моделью';
 return labels[event.type]||event.type;
}
