import {translate, displayText} from './i18n/core.ts';
import { useEffect, useState } from 'react';
import { Button, Spin } from 'antd';
import { api } from './store';
import { comparisonResponseMatches } from './replanComparison';
import { ReplanComparisonView } from './ReplanComparisonView';
import type { PlanComparisonResponse, Snapshot, Topology } from './types';
export function PlanComparisonPanel({ planId, snapshot, topology, connected }: {
    planId: string;
    snapshot: Snapshot;
    topology: Topology;
    connected: boolean;
}) {
    const [data, setData] = useState<PlanComparisonResponse | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false), [revision, setRevision] = useState(0);
    useEffect(() => {
        let cancelled = false;
        setData(null);
        setError('');
        if (!connected) {
            setLoading(false);
            return;
        }
        setLoading(true);
        api<PlanComparisonResponse>(`/plans/${encodeURIComponent(planId)}/comparison`).then(value => { if (!cancelled)
            setData(value); }).catch(e => { if (!cancelled)
            setError(e.message); }).finally(() => { if (!cancelled)
            setLoading(false); });
        return () => { cancelled = true; };
    }, [planId, snapshot.epoch, snapshot.active_plan_id, snapshot.constraint_version, snapshot.metrics.quality_signature, connected, revision]);
    const current = comparisonResponseMatches(data, snapshot, planId) ? data : null;
    return <div className="candidate-comparison">
  <div className="comparison-toolbar"><span>{translate("Сравнение с действующим планом")}</span><Button size="small" disabled={loading || !connected} onClick={() => setRevision(n => n + 1)}>{translate("Обновить оценку")}</Button></div>
  {displayText(!connected ? <p className="comparison-caution">{translate("Для новой оценки требуется подключение к серверу.")}</p> : error ? <p role="alert" className="comparison-caution">{displayText(error)}</p> : current ? <ReplanComparisonView comparison={current.comparison} snapshot={snapshot} topology={topology} mode="preview"/> : loading ? <Spin size="small"/> : <p className="comparison-note">{translate("Ожидаем актуальный снимок выбранного плана.")}</p>)}
 </div>;
}
