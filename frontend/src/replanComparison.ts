import {translate, displayText} from './i18n/core.ts';
import type { Metrics, PlanComparisonResponse, ReplanComparison, Snapshot } from './types.ts';
type MetricKey = 'index' | 'conflicts' | 'total_delay_s' | 'max_delay_s' | 'energy_kwh' | 'terminal_accuracy';
export interface ComparisonRow {
    key: MetricKey;
    label: string;
    unit: 'score' | 'count' | 'seconds' | 'kwh' | 'percent';
    before: number | null;
    after: number | null;
    delta: number | null;
}
export function sameQualityFormula(change: ReplanComparison): boolean {
    return !!change.before.quality_signature && change.before.quality_signature === change.after.quality_signature;
}
export function comparisonRows(change: ReplanComparison): ComparisonRow[] {
    const definitions: [
        MetricKey,
        string,
        ComparisonRow['unit']
    ][] = [
        ['index', translate("Индекс качества"), 'score'], ['conflicts', translate("Нарушения расписания"), 'count'],
        ['total_delay_s', translate("Суммарная задержка на станциях"), 'seconds'], ['max_delay_s', translate("Максимальная задержка"), 'seconds'],
        ['energy_kwh', translate("Энергия за весь сценарий"), 'kwh'], ['terminal_accuracy', translate("Точность конечного прибытия"), 'percent'],
    ];
    const value = (metrics: Metrics, key: MetricKey) => {
        const n = key === 'terminal_accuracy' ? metrics.terminal_arrivals?.on_time_pct : metrics[key];
        return typeof n === 'number' && Number.isFinite(n) ? n : null;
    };
    return definitions.map(([key, label, unit]) => {
        const before = value(change.before, key), after = value(change.after, key);
        const compatible = key !== 'index' || sameQualityFormula(change);
        return { key, label, unit, before, after, delta: compatible && before !== null && after !== null ? Math.round((after - before) * 1000) / 1000 : null };
    });
}
export function comparisonValue(value: number | null, unit: ComparisonRow['unit'], signed = false): string {
    if (value === null || !Number.isFinite(value))
        return '—';
    const n = Math.abs(value), prefix = value < 0 ? '−' : signed && value > 0 ? '+' : '';
    if (unit === 'seconds') {
        const seconds = Math.round(n), minutes = Math.floor(seconds / 60), remainder = seconds % 60;
        return prefix + (minutes ? translate("{0} мин{1}", minutes, remainder ? ' ' + remainder + translate(" с") : '') : translate("{0} с", remainder));
    }
    const rounded = Number(n.toFixed(unit === 'count' ? 0 : 1));
    const text = n > 0 && rounded === 0 ? '<0.1' : n.toFixed(unit === 'count' ? 0 : 1);
    return prefix + text + ({ score: translate(" балла"), count: '', kwh: translate(" кВт·ч"), percent: translate(" п.п.") } as const)[unit];
}
export function comparisonResponseMatches(data: PlanComparisonResponse | null, snapshot: Snapshot, planId: string): boolean {
    return !!data && data.epoch === snapshot.epoch && data.constraint_version === snapshot.constraint_version &&
        data.active_plan_id === snapshot.active_plan_id && data.plan_id === planId && data.sim_time_s <= snapshot.sim_time_s &&
        data.comparison.old_plan_id === snapshot.active_plan_id && data.comparison.new_plan_id === planId &&
        data.comparison.after.quality_signature === snapshot.metrics.quality_signature;
}
