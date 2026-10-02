import {translate, displayText} from './i18n/core.ts';
import { useEffect, useState } from 'react';
import { Button, Tag } from 'antd';
import { ShieldCheck } from 'lucide-react';
import { api, clock, minutes } from './store';
import type { DispatchDecision, DispatchReport, Plan, Snapshot, Topology } from './types';
const actions = { get passing() {
        return translate("Проходит");
    }, get completed() {
        return translate("Завершил");
    }, get held() {
        return translate("Удерживается");
    }, get wait() {
        return translate("Ожидает");
    }, get ready() {
        return translate("Готов по графику");
    } };
const policies: Record<string, string> = { get baseline() {
        return translate("Исходное расписание");
    }, get balanced() {
        return translate("Баланс задержек");
    }, get passenger_priority() {
        return translate("Приоритет пассажирских");
    }, get priority_order() {
        return translate("Резервный порядок по приоритету");
    }, get preserve_order() {
        return translate("Сохранение порядка");
    }, get eco() {
        return translate("Экономичный ход");
    } };
const kinds: Record<string, string> = { get opposing() {
        return translate("Встречные поезда");
    }, get following() {
        return translate("Попутные поезда");
    }, get switch() {
        return translate("Стрелочная горловина");
    }, get capacity() {
        return translate("Вместимость станции");
    }, get closed() {
        return translate("Закрытый перегон");
    }, get signal() {
        return translate("Запрет сигнала");
    }, get past() {
        return translate("Устаревшее отправление");
    }, get delay() {
        return translate("Задержка на станции");
    }, get route() {
        return translate("Неполный маршрут");
    }, get physics() {
        return translate("Время движения");
    }, get dwell() {
        return translate("Стоянка");
    }, get clearance() {
        return translate("Освобождение хвостом");
    } };
export function DispatcherPanel({ snapshot, topology, preview, historical, canControl, onCalculate }: {
    snapshot: Snapshot;
    topology: Topology;
    preview: Plan | null;
    historical: boolean;
    canControl: boolean;
    onCalculate: () => Promise<void>;
}) {
    const [report, setReport] = useState<DispatchReport | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0);
    const planId = preview?.id || snapshot.active_plan_id;
    useEffect(() => {
        let cancelled = false;
        setReport(null);
        setError('');
        if (!historical)
            api<DispatchReport>(`/dispatch?plan_id=${encodeURIComponent(planId)}`).then(value => { if (!cancelled)
                setReport(value); }).catch(e => { if (!cancelled)
                setError(e.message); });
        return () => { cancelled = true; };
    }, [planId, snapshot.epoch, historical, revision]);
    const summary = preview && !historical ? report : snapshot.dispatch;
    const number = (id: string) => snapshot.trains.find(t => t.id === id)?.number || id;
    const station = (id: string) => topology.stations.find(s => s.id === id)?.name || id;
    const section = (id: string) => { const s = topology.sections.find(s => s.id === id); return s ? `${station(s.from_station)} — ${station(s.to_station)}` : id; };
    const reason = (d: DispatchDecision) => {
        if (d.committed)
            return translate("Начатое движение зафиксировано");
        if (d.action === 'held')
            return translate("Отправление запрещено до допустимого плана");
        if (d.predecessors.length)
            return translate("По расписанию после: {0}", [...new Set(d.predecessors.map(p => `№ ${number(p.train_id)}`))].join(', '));
        return d.reason === 'ready_time' ? translate("Готовность поезда и обязательная стоянка") : translate("Согласование перегонов, стрелок и станционных путей");
    };
    return <section className="panel dispatcher-panel">
  <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17}/><h2>{translate("Автодиспетчер")}</h2><Tag color={summary?.valid ? 'green' : 'orange'}>{displayText(summary ? summary.valid ? translate("Проверка пройдена") : translate("Нарушений: {0}", summary.conflicts.length) : translate("Проверка…"))}</Tag></div>
   <div className="dispatch-panel-actions"><Button disabled={!canControl || snapshot.replanning} loading={snapshot.replanning} onClick={onCalculate}>{translate("Пауза и расчёт")}</Button></div>
  </div>
  <div className="dispatch-intro"><strong>{displayText(historical ? translate("Архив") : preview ? translate("Предпросмотр — ещё не применяется") : translate("Активный график"))} · {displayText(policies[summary?.policy || 'baseline'] || summary?.policy)}</strong>
   <p>{translate("«Пауза и расчёт» останавливает модель. Выберите вариант ниже, нажмите «Применить», затем «Запустить».")}</p>
   {displayText(preview && report && <p>{translate("Проверка на ")}{displayText(clock(report.sim_time_s))}: {displayText(report.applicable ? translate("план допустим на момент проверки") : translate("план нельзя применить сейчас"))}{translate(". Перед применением сервер проверит его повторно.")}</p>)}
   {displayText(error && <p role="alert">{displayText(error)} <Button size="small" onClick={() => setRevision(v => v + 1)}>{translate("Повторить проверку")}</Button></p>)}
   {displayText(historical && !summary && <p>{translate("Этот архивный снимок создан до добавления автодиспетчера.")}</p>)}
  </div>
  {displayText(!!summary?.conflicts.length && <ul className="dispatch-conflicts">{displayText(summary.conflicts.slice(0, 12).map((c, i) => <li key={i}>{displayText(kinds[c.kind] || c.code)}: {displayText(c.train_id && `№ ${number(c.train_id)}`)} {displayText(c.other_train_id && ` / № ${number(c.other_train_id)}`)} {displayText(c.section_id ? section(c.section_id) : c.station_id ? station(c.station_id) : c.resource?.startsWith('section') ? section(c.resource) : c.resource)} {displayText(c.start_s !== undefined && c.end_s !== undefined && `${clock(c.start_s)}–${clock(c.end_s)}`)}</li>))}{displayText(summary.conflicts.length > 12 && <li>{translate("Ещё ")}{displayText(summary.conflicts.length - 12)}{translate("; полный список доступен в API.")}</li>)}</ul>)}
  {displayText(!!summary?.next_decisions.length && <div className="dispatch-scroll"><table className="dispatch-table"><caption>{translate("Ближайшее решение для каждого поезда")}</caption><thead><tr><th>{translate("Поезд / приоритет")}</th><th>{translate("Решение")}</th><th>{translate("Перегон")}</th><th>{translate("Отправление / ожидание")}</th><th>{translate("Порядок по графику")}</th></tr></thead><tbody>{displayText(summary.next_decisions.map(d => <tr key={d.train_id}><td>№ {displayText(number(d.train_id))}<small>{translate("Приоритет ")}{displayText(d.priority)}{translate(" · штраф ×")}{displayText(d.lateness_weight)}</small></td><td>{displayText(actions[d.action])}</td><td>{displayText(section(d.section_id))}</td><td>{displayText(clock(d.start_s))}<small>{displayText(d.committed ? translate("Уже отправлен") : translate("Осталось {0}", minutes(d.remaining_wait_s)))}</small></td><td>{displayText(reason(d))}</td></tr>))}</tbody></table></div>)}
  {displayText(!historical && report && <details className="dispatch-schedule"><summary>{translate("Полное расписание · порядок освобождения перегонов")}</summary><p>{translate("Перегон занят до указанного времени освобождения хвостом, даже после прибытия головы поезда.")}</p>{displayText(report.section_order.map(group => <div key={group.section_id}><h3>{displayText(section(group.section_id))}</h3><div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>{translate("Порядок")}</th><th>{translate("Поезд")}</th><th>{translate("Отправление")}</th><th>{translate("Прибытие")}</th><th>{translate("Освобождение")}</th></tr></thead><tbody>{displayText(group.reservations.map((m, i) => <tr key={`${m.train_id}-${m.leg}`}><td>{displayText(i + 1)}</td><td>№ {displayText(number(m.train_id))}</td><td>{displayText(clock(m.start_s))}</td><td>{displayText(clock(m.end_s))}</td><td>{displayText(clock(m.release_s))}</td></tr>))}</tbody></table></div></div>))}</details>)}
 </section>;
}
