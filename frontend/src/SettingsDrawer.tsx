import {translate, displayText} from './i18n/core.ts';
import { useEffect, useState } from 'react';
import { App as AntApp, Button, Drawer, InputNumber, Select, Spin } from 'antd';
import { api } from './store';
import { qualityNames } from './quality';
import { numericSettings, qualityWeightKeys, settingsError, weightPercentages } from './qualitySettings';
import type { ScenarioSettings } from './types';
interface Props {
    open: boolean;
    epoch: string;
    canEdit: boolean;
    onClose: () => void;
    onSaved: () => Promise<void>;
}
export function SettingsDrawer({ open, epoch, canEdit, onClose, onSaved }: Props) {
    const { message } = AntApp.useApp();
    const [value, setValue] = useState<ScenarioSettings | null>(null), [loading, setLoading] = useState(false), [saving, setSaving] = useState(false), [error, setError] = useState(''), [revision, setRevision] = useState(0);
    useEffect(() => {
        if (!open)
            return;
        let cancelled = false;
        setLoading(true);
        setValue(null);
        setError('');
        api<ScenarioSettings>('/settings').then(data => { if (!cancelled)
            setValue(data); }).catch(e => { if (!cancelled)
            setError(e.message); }).finally(() => { if (!cancelled)
            setLoading(false); });
        return () => { cancelled = true; };
    }, [open, epoch, revision]);
    const disabled = !canEdit || saving;
    const patch = (changes: Partial<ScenarioSettings>) => setValue(current => current ? { ...current, ...changes } : null);
    const invalid = value ? settingsError(value) : null;
    const percentages = value ? weightPercentages(value.quality_weights) : null;
    const numeric = (key: keyof typeof numericSettings) => {
        const { label, min, max, step } = numericSettings[key];
        return <label key={key}>{displayText(label)}<InputNumber aria-label={displayText(label)} value={value![key]} min={min} max={max} step={step} disabled={disabled} onChange={n => patch({ [key]: n ?? 0 })}/></label>;
    };
    const save = async () => {
        if (!value || invalid || disabled)
            return;
        setSaving(true);
        setError('');
        try {
            await api('/settings', 'PUT', value);
            message.success(translate("Настройки применены. Индекс обновляется в эфире."));
            onClose();
            void onSaved().catch(e => message.error(translate("Настройки сохранены, но обновление данных не удалось: ") + e.message));
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setSaving(false);
        }
    };
    return <Drawer title={translate("Настройки сценария")} open={open} onClose={onClose} width={540}>
  {displayText(loading ? <Spin /> : value ? <div className="quality-settings">
   {displayText(!canEdit && <p className="subtle">{translate("Изменение доступно администратору в прямом эфире.")}</p>)}
   <h3>{translate("Формула индекса")}</h3>
   <Select aria-label={translate("Формула индекса")} value={value.quality_formula} disabled={disabled} onChange={quality_formula => patch({ quality_formula })} options={[{ value: 'weighted_mean', label: translate("Взвешенное среднее") }, { value: 'weighted_geometric', label: translate("Геометрическое среднее") }]}/>
   <p className="subtle">{displayText(value.quality_formula === 'weighted_mean' ? translate("Сумма оценок × их доли. Каждый показатель вносит отдельную потерю баллов.") : translate("Произведение оценок в степени их долей. Сильнее реагирует на слабые показатели; нулевая оценка с ненулевым весом обнуляет индекс."))}</p>
   <h3>{translate("Веса пяти показателей")}</h3>
   <p className="subtle">{translate("Любые относительные веса от 0 до 1000. Доли автоматически приводятся к 100%. Нулевой вес исключает показатель из индекса.")}</p>
   <div className="quality-weight-fields">{displayText(qualityWeightKeys.map(key => <label key={key}><span>{displayText(qualityNames[key])}<small>{displayText(percentages![key].toFixed(1))}{translate("% индекса")}</small></span><InputNumber aria-label={displayText(translate("Вес: ") + qualityNames[key])} min={0} max={1000} value={value.quality_weights[key]} disabled={disabled} onChange={n => patch({ quality_weights: { ...value.quality_weights, [key]: n ?? 0 } })}/></label>))}</div>
   <h3>{translate("Категории качества")}</h3>
   <div className="modal-fields"><label>{translate("Норма: от")}<InputNumber aria-label={translate("Порог нормы")} value={value.quality_threshold_normal} min={.1} max={100} step={1} disabled={disabled} onChange={n => patch({ quality_threshold_normal: n ?? 0 })}/></label><label>{translate("Внимание: от")}<InputNumber aria-label={translate("Порог внимания")} value={value.quality_threshold_attention} min={0} max={99.9} step={1} disabled={disabled} onChange={n => patch({ quality_threshold_attention: n ?? 0 })}/></label></div>
   <p className="subtle">{translate("Норма: ≥ ")}{displayText(value.quality_threshold_normal)}{translate(". Внимание: от ")}{displayText(value.quality_threshold_attention)}{translate(" до ")}{displayText(value.quality_threshold_normal)}{translate(". Критично: ниже ")}{displayText(value.quality_threshold_attention)}.</p>
   <details className="quality-formula"><summary>{translate("Нормирование и штрафы")}</summary><div className="modal-fields">{displayText((['delay_norm_s', 'energy_norm_kwh', 'arrival_tolerance_s', 'conflict_penalty'] as const).map(numeric))}</div></details>
   <details className="quality-formula"><summary>{translate("Приоритеты диспетчеризации")}</summary><p className="subtle">{translate("Определяют штраф за опоздание при выборе расписания. Веса индекса выше меняют его оценку.")}</p><div className="modal-fields">{displayText((['passenger_weight', 'freight_weight'] as const).map(numeric))}</div></details>
   <details className="quality-formula"><summary>{translate("Допущения модели")}</summary><ul className="assumptions"><li>{translate("Станции привязаны к связному маршруту OSM.")}</li><li>{translate("Однопутные перегоны, 2 пути на промежуточных станциях, 8 на конечных.")}</li><li>{translate("Лимит 90 км/ч; грузовые — до 72 км/ч. Минимальная стоянка 90 с.")}</li><li>{translate("Ровный профиль пути, без рекуперации. Параметры поездов заданы для демо.")}</li></ul></details>
   {displayText((invalid || error) && <p role="alert" className="settings-error">{displayText(invalid || error)}</p>)}
   <Button type="primary" disabled={disabled || !!invalid} loading={saving} onClick={save}>{translate("Применить настройки")}</Button>
   <p className="subtle">{translate("Изменения действуют в текущем запуске и сбрасывают рассчитанные варианты. Сброс сценария возвращает исходные настройки. Архивные оценки сохраняются со своей формулой.")}</p>
  </div> : <p role="alert">{displayText(error)} <Button onClick={() => setRevision(v => v + 1)}>{translate("Повторить")}</Button></p>)}
 </Drawer>;
}
