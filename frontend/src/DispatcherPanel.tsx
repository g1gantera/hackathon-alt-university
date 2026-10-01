import {useEffect,useState} from 'react';
import {Button,Tag} from 'antd';
import {ShieldCheck} from 'lucide-react';
import {api,clock,minutes} from './store';
import type {DispatchDecision,DispatchReport,Plan,Snapshot,Topology} from './types';

const actions={passing:'Проходит',completed:'Завершил',held:'Удерживается',wait:'Ожидает',ready:'Готов по графику'};
const policies:Record<string,string>={baseline:'Исходное расписание',balanced:'Баланс задержек',passenger_priority:'Приоритет пассажирских',priority_order:'Резервный порядок по приоритету',preserve_order:'Сохранение порядка',eco:'Экономичный ход'};
const kinds:Record<string,string>={opposing:'Встречные поезда',following:'Попутные поезда',switch:'Стрелочная горловина',capacity:'Вместимость станции',closed:'Закрытый перегон',signal:'Запрет сигнала',past:'Устаревшее отправление',delay:'Задержка на станции',route:'Неполный маршрут',physics:'Время движения',dwell:'Стоянка',clearance:'Освобождение хвостом'};

export function DispatcherPanel({snapshot,topology,preview,historical,canControl,onCalculate}:{snapshot:Snapshot;topology:Topology;preview:Plan|null;historical:boolean;canControl:boolean;onCalculate:()=>Promise<void>}) {
 const [report,setReport]=useState<DispatchReport|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 const planId=preview?.id||snapshot.active_plan_id;
 useEffect(()=>{
  let cancelled=false;
  setReport(null);setError('');
  if(!historical)api<DispatchReport>(`/dispatch?plan_id=${encodeURIComponent(planId)}`).then(value=>{if(!cancelled)setReport(value);}).catch(e=>{if(!cancelled)setError(e.message);});
  return()=>{cancelled=true;};
 },[planId,snapshot.epoch,historical,revision]);
 const summary=preview&&!historical?report:snapshot.dispatch;
 const number=(id:string)=>snapshot.trains.find(t=>t.id===id)?.number||id;
 const station=(id:string)=>topology.stations.find(s=>s.id===id)?.name||id;
 const section=(id:string)=>{const s=topology.sections.find(s=>s.id===id);return s?`${station(s.from_station)} — ${station(s.to_station)}`:id;};
 const reason=(d:DispatchDecision)=>{
  if(d.committed)return 'Начатое движение зафиксировано';
  if(d.action==='held')return 'Отправление запрещено до допустимого плана';
  if(d.predecessors.length)return `По расписанию после: ${[...new Set(d.predecessors.map(p=>`№ ${number(p.train_id)}`))].join(', ')}`;
  return d.reason==='ready_time'?'Готовность поезда и обязательная стоянка':'Согласование перегонов, стрелок и станционных путей';
 };
 return <section className="panel dispatcher-panel">
  <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17}/><h2>Автодиспетчер</h2><Tag color={summary?.valid?'green':'orange'}>{summary?summary.valid?'Проверка пройдена':`Нарушений: ${summary.conflicts.length}`:'Проверка…'}</Tag></div>
   <div className="dispatch-panel-actions"><Button disabled={!canControl||snapshot.replanning} loading={snapshot.replanning} onClick={onCalculate}>Пауза и расчёт</Button></div>
  </div>
  <div className="dispatch-intro"><strong>{historical?'Архив':preview?'Предпросмотр — ещё не применяется':'Активный график'} · {policies[summary?.policy||'baseline']||summary?.policy}</strong>
   <p>«Пауза и расчёт» останавливает модель. Выберите вариант ниже, нажмите «Применить», затем «Запустить».</p>
   {preview&&report&&<p>Проверка на {clock(report.sim_time_s)}: {report.applicable?'план допустим на момент проверки':'план нельзя применить сейчас'}. Перед применением сервер проверит его повторно.</p>}
   {error&&<p role="alert">{error} <Button size="small" onClick={()=>setRevision(v=>v+1)}>Повторить проверку</Button></p>}
   {historical&&!summary&&<p>Этот архивный снимок создан до добавления автодиспетчера.</p>}
  </div>
  {!!summary?.conflicts.length&&<ul className="dispatch-conflicts">{summary.conflicts.slice(0,12).map((c,i)=><li key={i}>{kinds[c.kind]||c.code}: {c.train_id&&`№ ${number(c.train_id)}`} {c.other_train_id&&` / № ${number(c.other_train_id)}`} {c.section_id?section(c.section_id):c.station_id?station(c.station_id):c.resource?.startsWith('section')?section(c.resource):c.resource} {c.start_s!==undefined&&c.end_s!==undefined&&`${clock(c.start_s)}–${clock(c.end_s)}`}</li>)}{summary.conflicts.length>12&&<li>Ещё {summary.conflicts.length-12}; полный список доступен в API.</li>}</ul>}
  {!!summary?.next_decisions.length&&<div className="dispatch-scroll"><table className="dispatch-table"><caption>Ближайшее решение для каждого поезда</caption><thead><tr><th>Поезд / приоритет</th><th>Решение</th><th>Перегон</th><th>Отправление / ожидание</th><th>Порядок по графику</th></tr></thead><tbody>{summary.next_decisions.map(d=><tr key={d.train_id}><td>№ {number(d.train_id)}<small>Приоритет {d.priority} · штраф ×{d.lateness_weight}</small></td><td>{actions[d.action]}</td><td>{section(d.section_id)}</td><td>{clock(d.start_s)}<small>{d.committed?'Уже отправлен':`Осталось ${minutes(d.remaining_wait_s)}`}</small></td><td>{reason(d)}</td></tr>)}</tbody></table></div>}
  {!historical&&report&&<details className="dispatch-schedule"><summary>Полное расписание · порядок освобождения перегонов</summary><p>Перегон занят до указанного времени освобождения хвостом, даже после прибытия головы поезда.</p>{report.section_order.map(group=><div key={group.section_id}><h3>{section(group.section_id)}</h3><div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>Порядок</th><th>Поезд</th><th>Отправление</th><th>Прибытие</th><th>Освобождение</th></tr></thead><tbody>{group.reservations.map((m,i)=><tr key={`${m.train_id}-${m.leg}`}><td>{i+1}</td><td>№ {number(m.train_id)}</td><td>{clock(m.start_s)}</td><td>{clock(m.end_s)}</td><td>{clock(m.release_s)}</td></tr>)}</tbody></table></div></div>)}</details>}
 </section>;
}
