import {useEffect,useState} from 'react';
import type {Snapshot} from './types';
import {api} from './store';
type Load={tracks:{resource_id:string;label:string;occupancy_fraction:number;train_entries:number;occupied_s:number}[]};
export function TrackLoad({snapshot}:{snapshot:Snapshot}){
 const [load,setLoad]=useState<Load|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;setLoad(null);setError('');if(snapshot.awaiting_plan||snapshot.plan.applicable===false)return;
 api<{report:Load}>('/logic/track-load?start_s=0&end_s=86400').then(v=>{if(active)setLoad(v.report);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[snapshot.epoch,snapshot.active_plan_id,snapshot.awaiting_plan,snapshot.plan.applicable]);
 return <section className="panel track-load"><h3>Занятость путей · первые 24 часа</h3><p>Прогноз по резервированиям, с освобождением хвоста и защитными интервалами. Это не измеренная нагрузка КТЖ.</p>{error&&<p role="alert">{error}</p>}{snapshot.awaiting_plan?<p>Ожидается проверенный план.</p>:<div>{load?[...load.tracks].sort((a,b)=>b.occupancy_fraction-a.occupancy_fraction).map(t=><section key={t.resource_id}><strong>{t.label}</strong><span>{(100*t.occupancy_fraction).toFixed(1)}% · {t.train_entries} проходов</span><progress max={1} value={t.occupancy_fraction}/></section>):<p>Расчёт нагрузки…</p>}</div>}</section>;
}
