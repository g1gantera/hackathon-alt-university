import {useState} from 'react';
import {App,Button,Checkbox,InputNumber,Select} from 'antd';
import {api} from './store';
import type {Snapshot} from './types';
type Addition={kind:'passenger'|'freight';direction:1|-1;ready_in_s:number};
export function FleetEditor({snapshot,disabled,onApplied}:{snapshot:Snapshot;disabled:boolean;onApplied:()=>Promise<void>}){
 const {message}=App.useApp();
 const [add,setAdd]=useState<Addition[]>([]),[remove,setRemove]=useState<string[]>([]),[kind,setKind]=useState<Addition['kind']>('passenger'),[direction,setDirection]=useState<1|-1>(1),[minutes,setMinutes]=useState(2),[busy,setBusy]=useState(false);
 const apply=async()=>{setBusy(true);try{await api('/fleet/batch','POST',{epoch:snapshot.epoch,add,remove});setAdd([]);setRemove([]);await onApplied();message.success('Состав и расписание обновлены');}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 return <section className="panel fleet-editor"><h3>Управление поездами</h3><label>Тип<Select value={kind} onChange={setKind} options={[{value:'passenger',label:'Пассажирский'},{value:'freight',label:'Грузовой'}]}/></label><label>Направление<Select value={direction} onChange={setDirection} options={[{value:1,label:kind==='freight'?'Кокшетау → Астана-1':'Кокшетау → Нурлы Жол'},{value:-1,label:kind==='freight'?'Астана-1 → Кокшетау':'Нурлы Жол → Кокшетау'}]}/></label><label>Готовность через, мин<InputNumber precision={0} min={1} max={240} value={minutes} onChange={v=>setMinutes(v||1)}/></label><Button disabled={disabled||busy||add.length>=5} onClick={()=>setAdd(a=>[...a,{kind,direction,ready_in_s:minutes*60}])}>＋ Добавить в пакет</Button>
 <div className="fleet-staged">{add.map((a,i)=><div key={i}>{a.kind==='passenger'?'Пассажирский':'Грузовой'} {a.direction===1?'→ Астана':'→ Кокшетау'} <button disabled={busy} aria-label={`Отменить добавление ${i+1}`} onClick={()=>setAdd(x=>x.filter((_,j)=>i!==j))}>×</button></div>)}</div>
 <strong>Удалить до допуска на участок</strong><div className="fleet-remove">{snapshot.trains.map(t=><Checkbox key={t.id} disabled={disabled||busy||t.on_network!==false||!['scheduled','queued'].includes(t.status)} checked={remove.includes(t.id)} onChange={e=>setRemove(x=>e.target.checked?[...x,t.id]:x.filter(id=>id!==t.id))}>{t.number}</Checkbox>)}</div>
 <Button type="primary" block disabled={disabled||(!add.length&&!remove.length)} loading={busy} onClick={apply}>Применить: +{add.length} / −{remove.length}</Button><p>Пакет применяется целиком после проверки путей. В сценарии — от 5 до 20 поездов. Начавшие рейс сохраняются.</p></section>;
}
