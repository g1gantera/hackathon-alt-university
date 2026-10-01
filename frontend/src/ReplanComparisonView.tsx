import {Tag} from 'antd';
import {clock} from './store';
import {qualityStatus} from './quality';
import {comparisonRows,comparisonValue,sameQualityFormula} from './replanComparison';
import type {ReplanComparison,Snapshot,Topology} from './types';

interface Props {comparison:ReplanComparison;snapshot:Snapshot;topology:Topology;mode:'review'|'applied'|'preview';historical?:boolean}

export function ReplanComparisonView({comparison:change,snapshot,topology,mode,historical=false}:Props){
 const rows=comparisonRows(change);
 const train=(id:string)=>snapshot.trains.find(t=>t.id===id)?.number||id;
 const station=(id:string)=>topology.stations.find(s=>s.id===id)?.name||id;
 const section=(id:string)=>{const s=topology.sections.find(s=>s.id===id);return s?`${station(s.from_station)} — ${station(s.to_station)}`:id;};
 const validBefore=change.before.forecast_valid,validAfter=change.after.forecast_valid;
 const duration=change.calculation_s;
 const title=mode==='applied'?'Результат применения плана':mode==='review'?'Рекомендуемый вариант':'Выбранный вариант';
 const status=qualityStatus(change.after);
 const stale=mode!=='applied'&&change.evaluated_at_s!==undefined&&change.evaluated_at_s<snapshot.sim_time_s;
 const time=(value:number|undefined)=>value===undefined?'—':clock(value);
 return <section className="replan-result" aria-label={title}>
  <div className="comparison-heading"><h3>{title}</h3><span>{historical?'Архив · ':''}Прогноз на {change.evaluated_at_s===undefined?'момент расчёта':clock(change.evaluated_at_s)}{duration!==undefined&&duration!==null&&<> · Последний расчёт {duration.toFixed(2)} с <Tag color={duration<=5?'green':'orange'}>{duration<=5?'≤ 5 с':'> 5 с'}</Tag></>}</span></div>
  <p className="comparison-plans">{change.old_plan_label||'Действовавший план'} → <strong>{change.new_plan_label||'Новый план'}</strong></p>
  <div className="comparison-highlights">{rows.slice(0,3).map(row=><div key={row.key}><span>{row.label}</span><strong>{comparisonValue(row.before,row.unit)} <span>→</span> {comparisonValue(row.after,row.unit)}</strong><small>Изменение: {comparisonValue(row.delta,row.unit,true)}</small>{row.key==='index'&&<Tag color={status.color}>{status.label}</Tag>}</div>)}</div>
  <div className="comparison-validation"><Tag color={validBefore===false?'orange':validBefore===true?'green':'default'}>До: {validBefore===false?'неприменим':validBefore===true?'проверка пройдена':'проверка не сохранена'}</Tag><Tag color={validAfter===false?'red':validAfter===true?'green':'default'}>После: {validAfter===false?'неприменим':validAfter===true?'проверка пройдена':'проверка не сохранена'}</Tag><span>Начатые движения сохранены: <b>{change.committed_preserved}{change.committed_total!==undefined&&` из ${change.committed_total}`}</b></span></div>
  <p className="comparison-note">Оба прогноза рассчитаны для всего сценария при одинаковых ограничениях. Оценки сохраняют параметры этого сравнения. «Изменение» = новый − старый. Фактические результаты доступны в истории.</p>
  {validBefore===false&&<p className="comparison-caution">Старый план неприменим при условиях сравнения. Его низкая задержка не означает, что он выполним: новый план может увеличить время ожидания, устраняя нарушения.</p>}
  {validAfter===false&&<p role="alert" className="comparison-caution">Выбранный план уже неприменим. Выполните новый расчёт перед применением.</p>}
  {!sameQualityFormula(change)&&<p className="comparison-caution">Формулы индекса различаются или не сохранены. Разница индекса не вычисляется.</p>}
  {stale&&<p className="comparison-note">Время модели продвинулось после оценки. Перед применением сервер заново проверит расписание.</p>}
  <details className="comparison-details"><summary>Все показатели и изменения расписания · {change.changes.length} движений</summary>
   <div className="dispatch-scroll"><table className="dispatch-table comparison-metrics"><caption>Прогноз на момент сравнения</caption><thead><tr><th>Показатель</th><th>До</th><th>После</th><th>Изменение</th></tr></thead><tbody>{rows.map(row=><tr key={row.key}><th scope="row">{row.label}</th><td>{comparisonValue(row.before,row.unit).replace(' п.п.','%')}</td><td>{comparisonValue(row.after,row.unit).replace(' п.п.','%')}</td><td>{comparisonValue(row.delta,row.unit,true)}</td></tr>)}</tbody></table></div>
   <div className="dispatch-scroll"><table className="dispatch-table"><caption>Конечное прибытие поездов</caption><thead><tr><th>Поезд</th><th>Старый план</th><th>Новый план</th><th>Изменение</th></tr></thead><tbody>{change.trains.map(t=><tr key={t.train_id}><th scope="row">№ {train(t.train_id)}</th><td>{clock(t.before_arrival_s)}</td><td>{clock(t.after_arrival_s)}</td><td>{comparisonValue(t.change_s,'seconds',true)}</td></tr>)}</tbody></table></div>
   {change.changes.length?<div className="dispatch-scroll"><table className="dispatch-table comparison-movements"><caption>Изменённые движения · до → после</caption><thead><tr><th>Поезд / перегон</th><th>Отправление</th><th>Прибытие</th><th>Освобождение хвостом</th></tr></thead><tbody>{change.changes.map(m=><tr key={`${m.train_id}-${m.leg}`}><th scope="row">№ {train(m.train_id)}<small>{section(m.section_id)}</small></th><td>{clock(m.before_s)} → {clock(m.after_s)}<small>{comparisonValue(m.change_s,'seconds',true)}</small></td><td>{time(m.before_arrival_s)} → {time(m.after_arrival_s)}</td><td>{time(m.before_release_s)} → {time(m.after_release_s)}</td></tr>)}</tbody></table></div>:<p className="comparison-note">Время отправления, прибытия и освобождения перегонов не изменилось.</p>}
  </details>
 </section>;
}
