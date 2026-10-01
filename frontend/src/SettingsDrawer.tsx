import {useEffect,useState} from 'react';
import {App as AntApp,Button,Drawer,InputNumber,Select,Spin} from 'antd';
import {api} from './store';
import {qualityNames} from './quality';
import {numericSettings,qualityWeightKeys,settingsError,weightPercentages} from './qualitySettings';
import type {ScenarioSettings} from './types';

interface Props {open:boolean;epoch:string;canEdit:boolean;onClose:()=>void;onSaved:()=>Promise<void>}

export function SettingsDrawer({open,epoch,canEdit,onClose,onSaved}:Props){
 const {message}=AntApp.useApp();
 const [value,setValue]=useState<ScenarioSettings|null>(null),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 useEffect(()=>{
  if(!open)return;
  let cancelled=false;
  setLoading(true);setValue(null);setError('');
  api<ScenarioSettings>('/settings').then(data=>{if(!cancelled)setValue(data);}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});
  return()=>{cancelled=true;};
 },[open,epoch,revision]);
 const disabled=!canEdit||saving;
 const patch=(changes:Partial<ScenarioSettings>)=>setValue(current=>current?{...current,...changes}:null);
 const invalid=value?settingsError(value):null;
 const percentages=value?weightPercentages(value.quality_weights):null;
 const numeric=(key:keyof typeof numericSettings)=>{
  const {label,min,max,step}=numericSettings[key];
  return <label key={key}>{label}<InputNumber aria-label={label} value={value![key]} min={min} max={max} step={step} disabled={disabled} onChange={n=>patch({[key]:n??0})}/></label>;
 };
 const save=async()=>{
  if(!value||invalid||disabled)return;
  setSaving(true);setError('');
  try{
   await api('/settings','PUT',value);
   message.success('Настройки применены. Индекс обновляется в эфире.');
   onClose();
   void onSaved().catch(e=>message.error('Настройки сохранены, но обновление данных не удалось: '+e.message));
  }catch(e){setError((e as Error).message);}
  finally{setSaving(false);}
 };
 return <Drawer title="Настройки сценария" open={open} onClose={onClose} width={540}>
  {loading?<Spin/>:value?<div className="quality-settings">
   {!canEdit&&<p className="subtle">Изменение доступно администратору в прямом эфире.</p>}
   <h3>Формула индекса</h3>
   <Select aria-label="Формула индекса" value={value.quality_formula} disabled={disabled} onChange={quality_formula=>patch({quality_formula})} options={[{value:'weighted_mean',label:'Взвешенное среднее'},{value:'weighted_geometric',label:'Геометрическое среднее'}]}/>
   <p className="subtle">{value.quality_formula==='weighted_mean'?'Сумма оценок × их доли. Каждый показатель вносит отдельную потерю баллов.':'Произведение оценок в степени их долей. Сильнее реагирует на слабые показатели; нулевая оценка с ненулевым весом обнуляет индекс.'}</p>
   <h3>Веса пяти показателей</h3>
   <p className="subtle">Любые относительные веса от 0 до 1000. Доли автоматически приводятся к 100%. Нулевой вес исключает показатель из индекса.</p>
   <div className="quality-weight-fields">{qualityWeightKeys.map(key=><label key={key}><span>{qualityNames[key]}<small>{percentages![key].toFixed(1)}% индекса</small></span><InputNumber aria-label={'Вес: '+qualityNames[key]} min={0} max={1000} value={value.quality_weights[key]} disabled={disabled} onChange={n=>patch({quality_weights:{...value.quality_weights,[key]:n??0}})}/></label>)}</div>
   <h3>Категории качества</h3>
   <div className="modal-fields"><label>Норма: от<InputNumber aria-label="Порог нормы" value={value.quality_threshold_normal} min={.1} max={100} step={1} disabled={disabled} onChange={n=>patch({quality_threshold_normal:n??0})}/></label><label>Внимание: от<InputNumber aria-label="Порог внимания" value={value.quality_threshold_attention} min={0} max={99.9} step={1} disabled={disabled} onChange={n=>patch({quality_threshold_attention:n??0})}/></label></div>
   <p className="subtle">Норма: ≥ {value.quality_threshold_normal}. Внимание: от {value.quality_threshold_attention} до {value.quality_threshold_normal}. Критично: ниже {value.quality_threshold_attention}.</p>
   <details className="quality-formula"><summary>Нормирование и штрафы</summary><div className="modal-fields">{(['delay_norm_s','energy_norm_kwh','arrival_tolerance_s','conflict_penalty'] as const).map(numeric)}</div></details>
   <details className="quality-formula"><summary>Приоритеты диспетчеризации</summary><p className="subtle">Определяют штраф за опоздание при выборе расписания. Веса индекса выше меняют его оценку.</p><div className="modal-fields">{(['passenger_weight','freight_weight'] as const).map(numeric)}</div></details>
   <details className="quality-formula"><summary>Допущения модели</summary><ul className="assumptions"><li>Станции привязаны к связному маршруту OSM.</li><li>Однопутные перегоны, 2 пути на промежуточных станциях, 8 на конечных.</li><li>Лимит 90 км/ч; грузовые — до 72 км/ч. Минимальная стоянка 90 с.</li><li>Ровный профиль пути, без рекуперации. Параметры поездов заданы для демо.</li></ul></details>
   {(invalid||error)&&<p role="alert" className="settings-error">{invalid||error}</p>}
   <Button type="primary" disabled={disabled||!!invalid} loading={saving} onClick={save}>Применить настройки</Button>
   <p className="subtle">Изменения действуют в текущем запуске и сбрасывают рассчитанные варианты. Сброс сценария возвращает исходные настройки. Архивные оценки сохраняются со своей формулой.</p>
  </div>:<p role="alert">{error} <Button onClick={()=>setRevision(v=>v+1)}>Повторить</Button></p>}
 </Drawer>;
}
