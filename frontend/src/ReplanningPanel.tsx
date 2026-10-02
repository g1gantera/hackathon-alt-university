import {translate, displayText} from './i18n/core.ts';
import { App as AntApp, Button, Select, Switch, Tag } from 'antd';
import { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { api, clock } from './store';
import { ReplanComparisonView } from './ReplanComparisonView';
import type { ReplanningOptions, Snapshot, Topology } from './types';
const statusNames = { get idle() {
        return translate("Ожидает событий");
    }, get queued() {
        return translate("События объединяются");
    }, get calculating() {
        return translate("Расчёт нового плана");
    }, get review() {
        return translate("Ожидает применения");
    }, get applied() {
        return translate("План применён");
    }, get failed() {
        return translate("Расчёт не завершён");
    } };
const kinds: Record<string, string> = { get delay() {
        return translate("Задержка поезда");
    }, get closure() {
        return translate("Закрытие перегона");
    }, get signal() {
        return translate("Неисправность сигнала");
    } };
const triggers: Record<string, string> = { get incident() {
        return translate("новый сбой");
    }, get resolved() {
        return translate("сбой устранён");
    }, get expiry() {
        return translate("срок сбоя истёк");
    }, get retry() {
        return translate("повторный расчёт");
    }, get manual() {
        return translate("ручной расчёт");
    }, get options() {
        return translate("настройки применения");
    }, get settings() {
        return translate("настройки сценария");
    } };
export function ReplanningPanel({ snapshot, topology, canControl, historical, refresh }: {
    snapshot: Snapshot;
    topology: Topology;
    canControl: boolean;
    historical: boolean;
    refresh: () => Promise<void>;
}) {
    const { message } = AntApp.useApp();
    const [busy, setBusy] = useState(false);
    const [showPast, setShowPast] = useState(false);
    const status = snapshot.replan_status;
    const options = snapshot.replanning_options;
    if (!status || !options)
        return null;
    const comparison = status.comparison;
    const send = async (path: string, method = 'POST', body?: unknown) => {
        setBusy(true);
        try {
            await api(path, method, body);
            await refresh();
        }
        catch (e) {
            message.error((e as Error).message);
        }
        finally {
            setBusy(false);
        }
    };
    const updateOptions = (change: Partial<ReplanningOptions>) => send('/replanning', 'PUT', { ...options, ...change });
    const station = (id: string) => topology.stations.find(s => s.id === id)?.name || id;
    const train = (id: string) => snapshot.trains.find(t => t.id === id)?.number || id;
    const target = (id: string) => { const s = topology.sections.find(s => s.id === id); return s ? `${station(s.from_station)} — ${station(s.to_station)}` : `№ ${train(id)}`; };
    const enabled = canControl && !busy;
    const activeIncidents = snapshot.incidents.filter(i => i.start_s <= snapshot.sim_time_s && i.end_s > snapshot.sim_time_s && i.resolved_s === undefined);
    const visibleIncidents = showPast ? snapshot.incidents : activeIncidents;
    return <section className="panel replan-panel">
  <div className="panel-heading"><div className="panel-title"><RotateCcw size={17}/><h2>{translate("Сбои и перепланирование")}</h2><Tag color={status.status === 'failed' ? 'red' : status.status === 'applied' ? 'green' : 'blue'}>{displayText(statusNames[status.status])}</Tag></div><span className="subtle">{translate("Активных сбоев: ")}{displayText(activeIncidents.length)}</span></div>
  <div className="replan-options"><label><Switch checked={options.auto_apply} disabled={!enabled} onChange={auto_apply => updateOptions({ auto_apply })} aria-label={translate("Автоматически применять новый план")}/>{translate(" Автоприменение")}</label><Select aria-label={translate("Политика автоматического перепланирования")} value={options.policy} disabled={!enabled} onChange={policy => updateOptions({ policy })} options={[{ value: 'balanced', label: translate("Баланс задержек") }, { value: 'passenger_priority', label: translate("Приоритет пассажирских") }]}/>{displayText(status.status === 'failed' && <Button disabled={!enabled || snapshot.replanning} onClick={() => send('/replanning/retry')}>{translate("Повторить расчёт")}</Button>)}{displayText(snapshot.incidents.length > activeIncidents.length && <Button type="text" onClick={() => setShowPast(v => !v)}>{displayText(showPast ? translate("Только активные") : translate("Прошедшие события"))}</Button>)}</div>
  <div className="replan-description">
   {displayText(snapshot.clock_held_for_replan && <p className="replan-hold">{translate("На скорости выше 60× время модели ждёт расчёта или применения плана. Для продолжения времени во время ожидания выберите 60× или меньше.")}</p>)}
   {displayText(status.message && <p role="alert">{displayText(status.message)}</p>)}
   {displayText(status.status === 'failed' && status.trigger && <small>{translate("Причина: ")}{displayText(triggers[status.trigger] || status.trigger)}{displayText(status.attempt ? translate(" · попытка {0}", status.attempt) : '')}</small>)}
   {displayText(status.status === 'applied' && !snapshot.running && !historical && <p>{translate("План готов. Симуляция на паузе — нажмите «Запустить», чтобы продолжить движение.")}</p>)}
  </div>
  {displayText(!!visibleIncidents.length && <div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>{translate("Сбой")}</th><th>{translate("Где")}</th><th>{translate("До")}</th><th>{translate("Статус")}</th><th></th></tr></thead><tbody>{displayText([...visibleIncidents].reverse().map(i => { const active = i.start_s <= snapshot.sim_time_s && i.end_s > snapshot.sim_time_s && i.resolved_s === undefined; return <tr key={i.id}><td>{displayText(kinds[i.kind])}</td><td>{displayText(target(i.target_id))}</td><td>{displayText(clock(i.end_s))}</td><td><Tag color={active ? 'orange' : 'default'}>{displayText(active ? translate("Активен") : i.resolved_s !== undefined ? translate("Устранён") : translate("Истёк"))}</Tag></td><td><Button size="small" disabled={!enabled || !active} onClick={() => send(`/incidents/${i.id}/resolve`)}>{translate("Устранить")}</Button></td></tr>; }))}</tbody></table></div>)}
  {displayText(comparison && <ReplanComparisonView comparison={comparison} snapshot={snapshot} topology={topology} historical={historical} mode={status.status === 'applied' ? 'applied' : 'review'}/>)}
 </section>;
}
