import {useEffect,useRef,useState} from 'react';
import {App,Button,Slider} from 'antd';
import {api,clock} from './store';
import type {Snapshot} from './types';
export function Timeline({live,onView,canControl}:{live:Snapshot;onView:(s:Snapshot|null)=>void;canControl:boolean}){
 const {message}=App.useApp();const [frames,setFrames]=useState<Snapshot[]>([]),[cursor,setCursor]=useState<number|null>(null),[busy,setBusy]=useState(false);
 const callback=useRef(onView);callback.current=onView;
 const epoch=useRef(live.epoch);
 useEffect(()=>{if(epoch.current!==live.epoch){epoch.current=live.epoch;setFrames([live]);setCursor(null);callback.current(null);return;}setFrames(old=>{if(cursor!==null)return old;return [...old.filter(s=>s.epoch===live.epoch&&s.sim_time_s>=live.sim_time_s-900&&s.state_version!==live.state_version),live].slice(-1801);});},[live,cursor]);
 const seek=(i:number)=>{setCursor(i);onView(frames[i]);};
 const back=async()=>{if(cursor!==null){seek(Math.max(0,cursor-1));return;}setBusy(true);try{const values=await api<Snapshot[]>('/history');const data=values.filter(s=>s.epoch===live.epoch);if(!data.length){message.info('История ещё не накопилась');return;}setFrames(data);const i=Math.max(0,data.length-2);setCursor(i);onView(data[i]);}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 const forward=async()=>{if(cursor!==null){if(cursor>=frames.length-1){setCursor(null);onView(null);}else seek(cursor+1);return;}setBusy(true);try{await api('/simulation/step','POST',{seconds:30});}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 return <section className="timeline panel"><div><strong>{cursor===null?'Прямой эфир':'Просмотр истории'}</strong><span>{clock(cursor===null?live.sim_time_s:frames[cursor]?.sim_time_s||0)}</span></div><div className="timeline-controls"><Button onClick={back} loading={busy} disabled={cursor===0}>← Назад</Button><Slider aria-label="Время симуляции" min={0} max={Math.max(1,frames.length-1)} value={cursor??Math.max(0,frames.length-1)} disabled={frames.length<2||busy} onChange={seek} tooltip={{formatter:i=>clock(frames[i||0]?.sim_time_s||0)}}/><Button onClick={forward} disabled={busy||(cursor===null&&!canControl)}>{cursor===null?'+30 с':'Вперёд →'}</Button><Button type={cursor!==null?'primary':'default'} onClick={()=>{setCursor(null);onView(null);}}>В эфир</Button></div><small>Назад и шкала показывают сохранённые снимки за 15 минут. «+30 с» продвигает модель и ставит её на паузу.</small></section>;
}
