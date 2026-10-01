import {useEffect,useState} from 'react';
import {App,Button,InputNumber,Select} from 'antd';
import {api} from './store';

export const metricLabels:Record<string,string>={punctuality:'Соблюдение графика',throughput:'Пропускная способность',energy:'Энергия',conflicts:'Отсутствие конфликтов',arrival_accuracy:'Точность прибытия'};
interface Config {formula:'weighted_sum'|'weighted_geometric';weights:Record<string,number>;delay_scale_s:number;energy_budget_kwh:number;arrival_tolerance_s:number;normal_threshold:number;attention_threshold:number}
export function MetricSettings({disabled,onSaved}:{disabled:boolean;onSaved:()=>void}){
 const {message}=App.useApp();const [config,setConfig]=useState<Config|null>(null),[saving,setSaving]=useState(false);
 useEffect(()=>{api<Config>('/logic/metric-config').then(setConfig).catch(e=>message.error(e.message));},[message]);
 if(!config)return <p>Загрузка настроек индекса…</p>;
 const sum=Object.values(config.weights).reduce((a,b)=>a+b,0);
 return <section><h3>Формула индекса</h3><div className="modal-fields"><label>Способ расчёта<Select disabled={disabled} value={config.formula} onChange={formula=>setConfig({...config,formula})} options={[{value:'weighted_sum',label:'100 × сумма вес × показатель'},{value:'weighted_geometric',label:'100 × произведение показатель в степени веса'}]}/></label>
 {Object.entries(metricLabels).map(([key,label])=><label key={key}>Вес: {label}<InputNumber min={0} max={1} step={.05} disabled={disabled} value={config.weights[key]} onChange={v=>setConfig({...config,weights:{...config.weights,[key]:v??0}})}/></label>)}
 {Object.entries({delay_scale_s:'Норма задержки, с',energy_budget_kwh:'Бюджет энергии, кВт·ч',arrival_tolerance_s:'Допуск прибытия, с',normal_threshold:'Норма от, баллов',attention_threshold:'Внимание от, баллов'}).map(([key,label])=><label key={key}>{label}<InputNumber disabled={disabled} min={0} value={config[key as keyof Config] as number} onChange={v=>setConfig({...config,[key]:v??0})}/></label>)}</div>
 <p>Сумма весов: {sum.toFixed(3)} (требуется 1). Ниже порога «Внимание» — «Критично». До первого прибытия индекс предварительный: известные веса нормируются заново.</p>
 <Button type="primary" loading={saving} disabled={disabled||Math.abs(sum-1)>1e-6} onClick={async()=>{setSaving(true);try{await api('/logic/metric-config','PUT',config);onSaved();message.success('Формула сохранена. Варианты нужно пересчитать.');}catch(e){message.error((e as Error).message);}finally{setSaving(false);}}}>Сохранить формулу и веса</Button></section>;
}
