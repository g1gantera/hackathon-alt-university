import {App as AntApp,Button,Select,Switch,Tag} from 'antd';
import {useState} from 'react';
import {RotateCcw} from 'lucide-react';
import {api,clock} from './store';
import {ReplanComparisonView} from './ReplanComparisonView';
import type {ReplanningOptions,Snapshot,Topology} from './types';

const statusNames={idle:'Ожидает событий',queued:'События объединяются',calculating:'Расчёт нового плана',review:'Ожидает применения',applied:'План применён',failed:'Расчёт не завершён'};
const kinds:Record<string,string>={delay:'Задержка поезда',closure:'Закрытие перегона',signal:'Неисправность сигнала'};
const triggers:Record<string,string>={incident:'новый сбой',resolved:'сбой устранён',expiry:'срок сбоя истёк',retry:'повторный расчёт',manual:'ручной расчёт',options:'настройки применения',settings:'настройки сценария'};

export function ReplanningPanel({snapshot,topology,canControl,historical,refresh}:{snapshot:Snapshot;topology:Topology;canControl:boolean;historical:boolean;refresh:()=>Promise<void>}){
 const {message}=AntApp.useApp();
 const [busy,setBusy]=useState(false);
 const [showPast,setShowPast]=useState(false);
 const status=snapshot.replan_status;
 const options=snapshot.replanning_options;
 if(!status||!options)return null;
 const comparison=status.comparison;
 const send=async(path:string,method='POST',body?:unknown)=>{
  setBusy(true);
  try{await api(path,method,body);await refresh();}catch(e){message.error((e as Error).message);}finally{setBusy(false);}
 };
 const updateOptions=(change:Partial<ReplanningOptions>)=>send('/replanning','PUT',{...options,...change});
 const station=(id:string)=>topology.stations.find(s=>s.id===id)?.name||id;
 const train=(id:string)=>snapshot.trains.find(t=>t.id===id)?.number||id;
 const target=(id:string)=>{const s=topology.sections.find(s=>s.id===id);return s?`${station(s.from_station)} — ${station(s.to_station)}`:`№ ${train(id)}`;};
 const enabled=canControl&&!busy;
 const activeIncidents=snapshot.incidents.filter(i=>i.start_s<=snapshot.sim_time_s&&i.end_s>snapshot.sim_time_s&&i.resolved_s===undefined);
 const visibleIncidents=showPast?snapshot.incidents:activeIncidents;
 return <section className="panel replan-panel">
  <div className="panel-heading"><div className="panel-title"><RotateCcw size={17}/><h2>Сбои и перепланирование</h2><Tag color={status.status==='failed'?'red':status.status==='applied'?'green':'blue'}>{statusNames[status.status]}</Tag></div><span className="subtle">Активных сбоев: {activeIncidents.length}</span></div>
  <div className="replan-options"><label><Switch checked={options.auto_apply} disabled={!enabled} onChange={auto_apply=>updateOptions({auto_apply})} aria-label="Автоматически применять новый план"/> Автоприменение</label><Select aria-label="Политика автоматического перепланирования" value={options.policy} disabled={!enabled} onChange={policy=>updateOptions({policy})} options={[{value:'balanced',label:'Баланс задержек'},{value:'passenger_priority',label:'Приоритет пассажирских'}]}/>{status.status==='failed'&&<Button disabled={!enabled||snapshot.replanning} onClick={()=>send('/replanning/retry')}>Повторить расчёт</Button>}{snapshot.incidents.length>activeIncidents.length&&<Button type="text" onClick={()=>setShowPast(v=>!v)}>{showPast?'Только активные':'Прошедшие события'}</Button>}</div>
  <div className="replan-description">
   {snapshot.clock_held_for_replan&&<p className="replan-hold">На скорости выше 60× время модели ждёт расчёта или применения плана. Для продолжения времени во время ожидания выберите 60× или меньше.</p>}
   {status.message&&<p role="alert">{status.message}</p>}
   {status.status==='failed'&&status.trigger&&<small>Причина: {triggers[status.trigger]||status.trigger}{status.attempt?` · попытка ${status.attempt}`:''}</small>}
   {status.status==='applied'&&!snapshot.running&&!historical&&<p>План готов. Симуляция на паузе — нажмите «Запустить», чтобы продолжить движение.</p>}
  </div>
  {!!visibleIncidents.length&&<div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>Сбой</th><th>Где</th><th>До</th><th>Статус</th><th></th></tr></thead><tbody>{[...visibleIncidents].reverse().map(i=>{const active=i.start_s<=snapshot.sim_time_s&&i.end_s>snapshot.sim_time_s&&i.resolved_s===undefined;return <tr key={i.id}><td>{kinds[i.kind]}</td><td>{target(i.target_id)}</td><td>{clock(i.end_s)}</td><td><Tag color={active?'orange':'default'}>{active?'Активен':i.resolved_s!==undefined?'Устранён':'Истёк'}</Tag></td><td><Button size="small" disabled={!enabled||!active} onClick={()=>send(`/incidents/${i.id}/resolve`)}>Устранить</Button></td></tr>;})}</tbody></table></div>}
  {comparison&&<ReplanComparisonView comparison={comparison} snapshot={snapshot} topology={topology} historical={historical} mode={status.status==='applied'?'applied':'review'}/>}
 </section>;
}
