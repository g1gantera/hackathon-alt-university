import {translate, displayText} from './i18n/core.ts';
import type { Metrics, QualityComponent, Topology } from './types';
import { clock, minutes } from './store';
import { Tag } from 'antd';
import { qualityNames, qualityDriver, qualityStatus } from './quality';
const violationNames: Record<string, string> = { get occupancy() {
        return translate("Перегон");
    }, get switch() {
        return translate("Стрелочная горловина");
    }, get capacity() {
        return translate("Вместимость станции");
    }, get closed() {
        return translate("Закрытие");
    }, get signal() {
        return translate("Сигнал");
    }, get delay() {
        return translate("Задержка");
    }, get past() {
        return translate("Устаревшее отправление");
    }, get physics() {
        return translate("Время движения");
    }, get dwell() {
        return translate("Стоянка");
    }, get clearance() {
        return translate("Освобождение пути");
    }, get route() {
        return translate("Маршрут");
    }, get horizon() {
        return translate("Горизонт расчёта");
    } };
export function QualityDetails({ metrics, topology }: {
    metrics: Metrics;
    topology?: Topology;
}) {
    const current = (metrics.quality_version || 1) >= 3;
    const capacity = metrics.capacity;
    const status = qualityStatus(metrics);
    const geometric = metrics.formula?.aggregation === 'weighted_geometric';
    const thresholds = metrics.formula?.thresholds ?? { normal: 90, attention: 70 };
    const arrivals = metrics.terminal_arrivals;
    const section = (id: string) => { const s = topology?.sections.find(s => s.id === id); const name = (id: string) => topology?.stations.find(s => s.id === id)?.name || id; return s ? name(s.from_station) + ' — ' + name(s.to_station) : id; };
    return <div className="quality-details">
  <div className="quality-total">{displayText(metrics.index.toFixed(1))} <small>/ 100 · {displayText(metrics.forecast ? translate("прогноз") : translate("текущий индекс"))}</small> <Tag color={status.color}>{displayText(status.label)}</Tag></div>
  <p className="subtle">{translate("Норма: ≥ ")}{displayText(thresholds.normal)}{translate(". Внимание: от ")}{displayText(thresholds.attention)}{translate(" до ")}{displayText(thresholds.normal)}{translate(". Критично: ниже ")}{displayText(thresholds.attention)}.</p>
  {displayText(geometric && <p className="subtle">{translate("Потери распределены пропорционально логарифмическому вкладу показателей. При нулевых оценках — между ними по весам.")}</p>)}
  {displayText(current ? <p>{displayText(qualityDriver(metrics))}{translate(". Оценки без данных нейтральны и отмечены ниже.")}</p> : <p>{translate("Архивная формула v")}{displayText(metrics.quality_version || 1)}{translate(". Её оценки не сравниваются с текущей формулой.")}</p>)}
  {displayText(metrics.components && <div className="dispatch-scroll"><table className="dispatch-table quality-scores"><thead><tr><th>{translate("Показатель")}</th><th>{translate("Оценка / 100")}</th><th>{translate("Вес")}</th><th>{translate("Потеря баллов")}</th></tr></thead><tbody>{displayText(Object.entries(metrics.components).map(([key, item]) => item && <tr key={key}><td>{displayText(!current && key === 'schedule' ? translate("Соблюдение графика") : !current && key === 'arrival_accuracy' ? translate("Прибытия на станции") : qualityNames[key as QualityComponent])}{displayText(item.observed === false && <small>{translate("Пока нет данных · нейтрально")}</small>)}</td><td><b>{displayText(item.score.toFixed(1))}</b><progress aria-label={displayText(qualityNames[key as QualityComponent])} max={100} value={item.score}/></td><td>{displayText((item.weight * 100).toFixed(0))}%</td><td>{displayText(item.loss_points !== undefined ? '−' + item.loss_points.toFixed(1) : '—')}</td></tr>))}</tbody></table></div>)}
  <dl className="quality-facts">
   <div><dt>{translate("Задержка по прибытиям на станции")}</dt><dd>{displayText(minutes(metrics.total_delay_s))}</dd></div>
   {displayText(metrics.departures && <div><dt>{translate("Отклонение отправлений")}</dt><dd>{displayText(minutes(metrics.departures.deviation_s))} · {displayText(metrics.departures.evaluated)}{translate(" оценено")}</dd></div>)}
   <div><dt>{translate("Перегоны без сбоев")}</dt><dd>{displayText(metrics.available_sections ?? '—')} / {displayText(metrics.total_sections ?? '—')}</dd></div>
   <div><dt>{translate("Нарушения текущего расписания")}</dt><dd>{displayText(metrics.conflicts)}</dd></div>
   <div><dt>{displayText(current ? translate("Точность конечного прибытия") : translate("Точность прибытия"))}</dt><dd>{displayText(current ? (arrivals?.on_time_pct === null ? translate("Пока нет данных") : arrivals?.on_time_pct + '%') : (metrics.on_time_pct === null ? translate("Пока нет данных") : `${metrics.on_time_pct}%`))}</dd></div>
   {displayText(arrivals && <div><dt>{translate("Конечные прибытия: вовремя / оценено")}</dt><dd>{displayText(arrivals.on_time)} / {displayText(arrivals.evaluated)}{translate(" · ожидаются ")}{displayText(arrivals.pending)}</dd></div>)}
   <div><dt>{translate("Энергия / исходный план на том же пути")}</dt><dd>{displayText(metrics.energy_kwh.toFixed(1))} / {displayText(metrics.reference_energy_kwh?.toFixed(1) ?? '—')}{translate(" кВт·ч")}</dd></div>
   {displayText(metrics.energy && <div><dt>{displayText(metrics.energy.saved_kwh >= 0 ? translate("Экономия энергии") : translate("Перерасход энергии"))}</dt><dd>{displayText(Math.abs(metrics.energy.saved_kwh).toFixed(1))}{translate(" кВт·ч")}{displayText(metrics.energy.saved_pct !== null ? ' · ' + Math.abs(metrics.energy.saved_pct).toFixed(2) + '%' : '')}</dd></div>)}
  </dl>
  {displayText(capacity && <details className="quality-capacity" open><summary>{translate("Использование перегонов · ")}{displayText(metrics.forecast ? translate("горизонт исходного плана") : translate("последние 15 минут"))}</summary><p>{displayText(clock(capacity.window_start_s))} — {displayText(clock(capacity.window_end_s))}{translate(". Занято ")}{displayText(capacity.utilization_pct === null ? '—' : capacity.utilization_pct.toFixed(1) + '%')}{translate(" времени путей. Пройдено ")}{displayText((capacity.distance_m / 1000).toFixed(1))}{translate(" из ")}{displayText((capacity.reference_distance_m / 1000).toFixed(1))}{translate(" поездо-км по исходному графику.")}</p><div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>{translate("Перегон")}</th><th>{translate("Сбой сейчас")}</th><th>{translate("Занятость")}</th><th>{translate("Проезд / план, км")}</th></tr></thead><tbody>{displayText(capacity.sections.map(s => <tr key={s.section_id}><td>{displayText(section(s.section_id))}</td><td>{displayText(s.blocked_now ? translate("Действует") : translate("Нет"))}</td><td>{displayText(s.utilization_pct === null ? '—' : s.utilization_pct.toFixed(1) + '%')}</td><td>{displayText((s.distance_m / 1000).toFixed(1))} / {displayText((s.reference_distance_m / 1000).toFixed(1))}</td></tr>))}</tbody></table></div><p>{translate("Высокая занятость сама по себе не повышает индекс. Сравнивается выполненный проезд с исходным графиком; доля перегонов без сбоев учитывается отдельно. Это модель использования участка, не нормативная пропускная способность.")}</p></details>)}
  {displayText(metrics.conflict_summary && <details className="quality-conflicts"><summary>{translate("Проверка расписания · ")}{displayText(metrics.conflict_summary.total)}{translate(" нарушений")}</summary><p>{translate("Конфликты ресурсов: ")}{displayText(metrics.conflict_summary.resource_conflicts)}{translate("; ограничения сбоев: ")}{displayText(metrics.conflict_summary.restriction_violations)}{translate("; остальные проверки: ")}{displayText(metrics.conflict_summary.other_violations)}{translate(". Это нарушения плана, а не зарегистрированные столкновения.")}</p>{displayText(Object.entries(metrics.conflict_summary.by_code).map(([code, count]) => <p key={code}>{displayText(violationNames[code] || code)}: {displayText(count)}</p>))}</details>)}
  <details className="quality-formula"><summary>{translate("Формула и допущения")}</summary>{displayText(current ? <><p>{displayText(geometric ? translate("Индекс = 100 × произведение (оценка / 100) в степени доли показателя. Нулевая оценка с ненулевым весом обнуляет индекс.") : translate("Индекс = сумма (доля × оценка)."))}{translate(" Отправления: 100 × max(0, 1 − сумма абсолютных отклонений / ")}{displayText(metrics.formula?.delay_norm_s)}{translate(" с). Просроченные отправления учитываются до фактического выезда.")}</p><p>{translate("Использование участка: доля перегонов без закрытия или сбоя сигнала × min(100, выполненный проезд / исходный проезд × 100). До ожидаемого движения проезд нейтрален. Занятость — доля времени от въезда до освобождения хвостом, с объединением перекрывающихся интервалов.")}</p><p>{translate("Энергия: 100 × max(0, 1 − перерасход / ")}{displayText(metrics.formula?.energy_norm_kwh)}{translate(" кВт·ч). Расход сравнивается на одинаковом пройденном пути. Экономия показывается отдельно: оценка ограничена 100.")}</p><p>{translate("Нарушения: 100 / (1 + ")}{displayText(metrics.formula?.conflict_penalty ?? 1)}{translate(" × число нарушений расписания). Конечное прибытие: доля поездов в пределах ±")}{displayText(metrics.formula?.arrival_tolerance_s)}{translate(" с исходного расписания. Просроченное незавершённое прибытие считается опозданием.")}</p><p>{translate("Демо-формула v")}{displayText(metrics.quality_version)}: {displayText(geometric ? translate("геометрическое среднее") : translate("взвешенное среднее"))}{translate(". Доли показателей приведены в таблице. Настройки сохранены вместе с оценкой. Текущие показатели и прогноз используют разные периоды и не являются оценкой выигрыша друг относительно друга.")}</p></> : <p>{translate("Архивная формула учитывала задержки на станциях, энергию, открытые перегоны и нарушения. Снимок сохранён без пересчёта.")}</p>)}</details>
 </div>;
}
