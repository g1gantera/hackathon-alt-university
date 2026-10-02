import {translate, displayText} from './i18n/core.ts';
import type { Snapshot } from './types';
export interface HistoryFrame {
    id: number;
    sim_time_s: number;
    saved_at: number;
    state_version: number;
    quality_index: number;
    plan_id: string;
}
export interface HistoryEvent {
    id: number;
    sim_time_s: number;
    state_version: number;
    type: string;
    kind: string | null;
    target_id: string | null;
    action: string | null;
}
export interface HistoryWindow {
    epoch: string;
    from_s: number;
    to_s: number;
    through_id: number;
    minutes: 5 | 10 | 15;
    retention_hours: number;
    frames: HistoryFrame[];
    events: HistoryEvent[];
}
export interface HistoryRun {
    epoch: string;
    first_time_s: number;
    last_time_s: number;
    saved_at: number;
    snapshot_count: number;
}
export function matchesFrame(snapshot: Snapshot, epoch: string, frame: HistoryFrame) {
    return snapshot.epoch === epoch && snapshot.state_version === frame.state_version && snapshot.sim_time_s === frame.sim_time_s;
}
export function eventFrame(frames: HistoryFrame[], event: HistoryEvent) {
    // Incident/replan events are persisted before the corresponding snapshot.
    const index = frames.findIndex(frame => frame.state_version >= event.state_version);
    return index < 0 ? Math.max(0, frames.length - 1) : index;
}
export function reportUrl(window: HistoryWindow) {
    return '/api/reports/history.csv?' + new URLSearchParams({ epoch: window.epoch, minutes: String(window.minutes), to: String(window.to_s), through_id: String(window.through_id) });
}
const labels: Record<string, string> = {
    get 'incident.created'() {
        return translate("Добавлен сбой");
    },
    get 'incident.resolved'() {
        return translate("Сбой устранён");
    },
    get 'incident.expired'() {
        return translate("Сбой завершился");
    },
    get 'replan.queued'() {
        return translate("Перерасчёт в очереди");
    },
    get 'replan.started'() {
        return translate("Начат перерасчёт");
    },
    get 'replan.completed'() {
        return translate("Варианты рассчитаны");
    },
    get 'replan.failed'() {
        return translate("Перерасчёт не завершён");
    },
    get 'plan.applied'() {
        return translate("Применён план");
    },
    get 'eco.plan_ready'() {
        return translate("Готов экономичный план");
    },
    get 'settings.updated'() {
        return translate("Изменены настройки");
    },
    get 'replan.options_changed'() {
        return translate("Изменён режим диспетчера");
    },
};
export function eventLabel(event: HistoryEvent) {
    if (event.type === 'simulation.changed')
        return ({ start: translate("Запуск"), pause: translate("Пауза"), reset: translate("Новый запуск"), speed: translate("Изменена скорость времени") } as Record<string, string>)[event.action || ''] || translate("Управление моделью");
    return labels[event.type] || event.type;
}
