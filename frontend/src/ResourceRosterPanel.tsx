import {useEffect, useState} from 'react';
import {App, Button, Input, InputNumber, Select} from 'antd';
import {api} from './store';
import type {Snapshot, Topology} from './types';

type Unit = {id:string; kind:'locomotive'|'wagons'|'crew'; initial_station:string; available_s:number; unavailable_s:number; turnaround_s:number; train_ids:string[]; max_mass_kg?:number|null; max_length_m?:number|null};
type Roster = {source:string; units:Unit[]};
const kinds = [{value:'locomotive',label:'Локомотив'},{value:'wagons',label:'Состав'},{value:'crew',label:'Бригада'}];
export function ResourceRosterPanel({snapshot,topology,disabled}:{snapshot:Snapshot;topology:Topology;disabled:boolean}) {
 const {message}=App.useApp();
 const [roster,setRoster]=useState<Roster|null>(null),[enforced,setEnforced]=useState(false),[busy,setBusy]=useState(false);
 const locked=disabled||busy||snapshot.sim_time_s>0;
 useEffect(()=>{let alive=true;setRoster(null);setEnforced(false);api<{resource_roster:Roster|null}>('/logic/operations').then(r=>{if(alive){setRoster(r.resource_roster);setEnforced(!!r.resource_roster);}}).catch(e=>{if(alive)message.error(e.message);});return()=>{alive=false;};},[snapshot.epoch]);
 const update=(index:number,change:Partial<Unit>)=>setRoster(r=>r?{...r,units:r.units.map((u,i)=>i===index?{...u,...change}:u)}:r);
 async function save(value:Roster|null){setBusy(true);try{await api('/dispatch/resource-roster','POST',{epoch:snapshot.epoch,roster:value});setEnforced(!!value);if(!value)setRoster(null);message.success('Ограничения сохранены; график требует нового расчёта');}catch(e){message.error((e as Error).message);}finally{setBusy(false);}}
 return <details><summary>Парк и смены бригад · {enforced?'ограничения включены':'не заданы'}</summary>
 <p>Для каждого рейса назначьте локомотив, состав и бригаду. Один ресурс выполняет рейсы в выбранном порядке и остаётся на конечной станции. Назначения редактируются до начала движения.</p>
 <Button disabled={locked} onClick={async()=>{setBusy(true);try{setRoster(await api<Roster>('/logic/resource-roster-template'));}catch(e){message.error((e as Error).message);}finally{setBusy(false);}}}>Заполнить модельный парк</Button>
 <p>Шаблон выделяет отдельную технику каждому поезду. Смена бригады — 8 часов по допущению модели. Нажмите «Применить», чтобы включить ограничения.</p>
 {roster&&<><label>Источник / описание<Input disabled={locked} value={roster.source} onChange={e=>setRoster({...roster,source:e.target.value})}/></label>
 <div style={{overflow:'auto',maxHeight:420}}><table><thead><tr>{['Ресурс','Тип','Начальная станция','Доступен с, с','Доступен до, с','Оборот, с','Рейсы по порядку',''].map((t,i)=><th key={i}>{t}</th>)}</tr></thead><tbody>{roster.units.map((u,i)=><tr key={i}>
 <td><Input aria-label={`Ресурс ${i+1}`} disabled={locked} value={u.id} onChange={e=>update(i,{id:e.target.value})} style={{minWidth:160}}/></td>
 <td><Select disabled={locked} value={u.kind} options={kinds} onChange={kind=>update(i,{kind})} style={{minWidth:130}}/></td>
 <td><Select showSearch optionFilterProp="label" disabled={locked} value={u.initial_station} options={topology.stations.map(s=>({value:s.id,label:s.name}))} onChange={initial_station=>update(i,{initial_station})} style={{minWidth:170}}/></td>
 <td><InputNumber disabled={locked} min={0} precision={0} value={u.available_s} onChange={v=>update(i,{available_s:v??0})}/></td>
 <td><InputNumber disabled={locked} min={1} precision={0} value={u.unavailable_s} onChange={v=>update(i,{unavailable_s:v??1})}/></td>
 <td><InputNumber disabled={locked} min={0} precision={0} value={u.turnaround_s} onChange={v=>update(i,{turnaround_s:v??0})}/></td>
 <td><Select mode="multiple" disabled={locked} value={u.train_ids} options={snapshot.trains.map(t=>({value:t.id,label:t.number}))} onChange={train_ids=>update(i,{train_ids})} style={{minWidth:230}}/></td>
 <td><Button disabled={locked} onClick={()=>setRoster({...roster,units:roster.units.filter((_,j)=>j!==i)})}>Убрать ресурс</Button></td>
 </tr>)}</tbody></table></div>
 <Button disabled={locked} onClick={()=>setRoster({...roster,units:[...roster.units,{id:`MODEL-${Date.now()}`,kind:'locomotive',initial_station:topology.stations[0].id,available_s:0,unavailable_s:86400,turnaround_s:1800,train_ids:[]}]})}>Добавить ресурс</Button>
 <Button disabled={locked} onClick={()=>save(roster)}>Применить ограничения</Button></>}
 <Button disabled={locked||!enforced} onClick={()=>save(null)}>Снять ограничения парка</Button>
 <p>Время отсчитывается от начала симуляции. Автоматический подбор техники и смена бригады внутри одного рейса пока не поддерживаются.</p>
 </details>;
}
