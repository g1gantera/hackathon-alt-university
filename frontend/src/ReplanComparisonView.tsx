import {translate, displayText} from './i18n/core.ts';
import { Tag } from 'antd';
import { clock } from './store';
import { qualityStatus } from './quality';
import { comparisonRows, comparisonValue, sameQualityFormula } from './replanComparison';
import type { ReplanComparison, Snapshot, Topology } from './types';
interface Props {
    comparison: ReplanComparison;
    snapshot: Snapshot;
    topology: Topology;
    mode: 'review' | 'applied' | 'preview';
    historical?: boolean;
}
export function ReplanComparisonView({ comparison: change, snapshot, topology, mode, historical = false }: Props) {
    const rows = comparisonRows(change);
    const train = (id: string) => snapshot.trains.find(t => t.id === id)?.number || id;
    const station = (id: string) => topology.stations.find(s => s.id === id)?.name || id;
    const section = (id: string) => { const s = topology.sections.find(s => s.id === id); return s ? `${station(s.from_station)} — ${station(s.to_station)}` : id; };
    const validBefore = change.before.forecast_valid, validAfter = change.after.forecast_valid;
    const duration = change.calculation_s;
    const title = mode === 'applied' ? translate("Результат применения плана") : mode === 'review' ? translate("Рекомендуемый вариант") : translate("Выбранный вариант");
    const status = qualityStatus(change.after);
    const stale = mode !== 'applied' && change.evaluated_at_s !== undefined && change.evaluated_at_s < snapshot.sim_time_s;
    const time = (value: number | undefined) => value === undefined ? '—' : clock(value);
    return <section className="replan-result" aria-label={displayText(title)}>
  <div className="comparison-heading"><h3>{displayText(title)}</h3><span>{displayText(historical ? translate("Архив · ") : '')}{translate("Прогноз на ")}{displayText(change.evaluated_at_s === undefined ? translate("момент расчёта") : clock(change.evaluated_at_s))}{displayText(duration !== undefined && duration !== null && <>{translate(" · Последний расчёт ")}{displayText(duration.toFixed(2))}{translate(" с ")}<Tag color={duration <= 5 ? 'green' : 'orange'}>{displayText(duration <= 5 ? translate("≤ 5 с") : translate("> 5 с"))}</Tag></>)}</span></div>
  <p className="comparison-plans">{displayText(change.old_plan_label || translate("Действовавший план"))} → <strong>{displayText(change.new_plan_label || translate("Новый план"))}</strong></p>
  <div className="comparison-highlights">{displayText(rows.slice(0, 3).map(row => <div key={row.key}><span>{displayText(row.label)}</span><strong>{displayText(comparisonValue(row.before, row.unit))} <span>→</span> {displayText(comparisonValue(row.after, row.unit))}</strong><small>{translate("Изменение: ")}{displayText(comparisonValue(row.delta, row.unit, true))}</small>{displayText(row.key === 'index' && <Tag color={status.color}>{displayText(status.label)}</Tag>)}</div>))}</div>
  <div className="comparison-validation"><Tag color={validBefore === false ? 'orange' : validBefore === true ? 'green' : 'default'}>{translate("До: ")}{displayText(validBefore === false ? translate("неприменим") : validBefore === true ? translate("проверка пройдена") : translate("проверка не сохранена"))}</Tag><Tag color={validAfter === false ? 'red' : validAfter === true ? 'green' : 'default'}>{translate("После: ")}{displayText(validAfter === false ? translate("неприменим") : validAfter === true ? translate("проверка пройдена") : translate("проверка не сохранена"))}</Tag><span>{translate("Начатые движения сохранены: ")}<b>{displayText(change.committed_preserved)}{displayText(change.committed_total !== undefined && translate(" из {0}", change.committed_total))}</b></span></div>
  <p className="comparison-note">{translate("Оба прогноза рассчитаны для всего сценария при одинаковых ограничениях. Оценки сохраняют параметры этого сравнения. «Изменение» = новый − старый. Фактические результаты доступны в истории.")}</p>
  {displayText(validBefore === false && <p className="comparison-caution">{translate("Старый план неприменим при условиях сравнения. Его низкая задержка не означает, что он выполним: новый план может увеличить время ожидания, устраняя нарушения.")}</p>)}
  {displayText(validAfter === false && <p role="alert" className="comparison-caution">{translate("Выбранный план уже неприменим. Выполните новый расчёт перед применением.")}</p>)}
  {displayText(!sameQualityFormula(change) && <p className="comparison-caution">{translate("Формулы индекса различаются или не сохранены. Разница индекса не вычисляется.")}</p>)}
  {displayText(stale && <p className="comparison-note">{translate("Время модели продвинулось после оценки. Перед применением сервер заново проверит расписание.")}</p>)}
  <details className="comparison-details"><summary>{translate("Все показатели и изменения расписания · ")}{displayText(change.changes.length)}{translate(" движений")}</summary>
   <div className="dispatch-scroll"><table className="dispatch-table comparison-metrics"><caption>{translate("Прогноз на момент сравнения")}</caption><thead><tr><th>{translate("Показатель")}</th><th>{translate("До")}</th><th>{translate("После")}</th><th>{translate("Изменение")}</th></tr></thead><tbody>{displayText(rows.map(row => <tr key={row.key}><th scope="row">{displayText(row.label)}</th><td>{displayText(comparisonValue(row.before, row.unit).replace(translate(" п.п."), '%'))}</td><td>{displayText(comparisonValue(row.after, row.unit).replace(translate(" п.п."), '%'))}</td><td>{displayText(comparisonValue(row.delta, row.unit, true))}</td></tr>))}</tbody></table></div>
   <div className="dispatch-scroll"><table className="dispatch-table"><caption>{translate("Конечное прибытие поездов")}</caption><thead><tr><th>{translate("Поезд")}</th><th>{translate("Старый план")}</th><th>{translate("Новый план")}</th><th>{translate("Изменение")}</th></tr></thead><tbody>{displayText(change.trains.map(t => <tr key={t.train_id}><th scope="row">№ {displayText(train(t.train_id))}</th><td>{displayText(clock(t.before_arrival_s))}</td><td>{displayText(clock(t.after_arrival_s))}</td><td>{displayText(comparisonValue(t.change_s, 'seconds', true))}</td></tr>))}</tbody></table></div>
   {displayText(change.changes.length ? <div className="dispatch-scroll"><table className="dispatch-table comparison-movements"><caption>{translate("Изменённые движения · до → после")}</caption><thead><tr><th>{translate("Поезд / перегон")}</th><th>{translate("Отправление")}</th><th>{translate("Прибытие")}</th><th>{translate("Освобождение хвостом")}</th></tr></thead><tbody>{displayText(change.changes.map(m => <tr key={`${m.train_id}-${m.leg}`}><th scope="row">№ {displayText(train(m.train_id))}<small>{displayText(section(m.section_id))}</small></th><td>{displayText(clock(m.before_s))} → {displayText(clock(m.after_s))}<small>{displayText(comparisonValue(m.change_s, 'seconds', true))}</small></td><td>{displayText(time(m.before_arrival_s))} → {displayText(time(m.after_arrival_s))}</td><td>{displayText(time(m.before_release_s))} → {displayText(time(m.after_release_s))}</td></tr>))}</tbody></table></div> : <p className="comparison-note">{translate("Время отправления, прибытия и освобождения перегонов не изменилось.")}</p>)}
  </details>
 </section>;
}
