import {useEffect,useRef,useState} from 'react';
import {Button} from 'antd';
import {api,clock} from './store';
import {QualityDetails} from './QualityDetails';
import {QualityChart} from './Charts';
import {qualityResponseMatches,qualityTrend} from './quality';
import type {QualityResponse,Snapshot,Topology} from './types';

export function QualityPanel({snapshot,topology,historical,active,connected}:{snapshot:Snapshot;topology:Topology;historical:boolean;active:boolean;connected:boolean}) {
 const [data,setData]=useState<QualityResponse|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 const latest=useRef(snapshot);latest.current=snapshot;
 useEffect(()=>{
  let cancelled=false;
  let timer:ReturnType<typeof setTimeout>;
  setData(null);setError('');
  if(historical||!active||!connected)return;
  const refresh=async()=>{
   try{const result=await api<QualityResponse>('/quality');if(!cancelled&&qualityResponseMatches(result,latest.current)){setData(result);setError('');}}
   catch(e){if(!cancelled){setError((e as Error).message);setData(null);}}
   finally{if(!cancelled)timer=setTimeout(refresh,5000);}
  };
  void refresh();
  return()=>{cancelled=true;clearTimeout(timer);};
 },[snapshot.epoch,snapshot.active_plan_id,snapshot.constraint_version,snapshot.metrics.quality_signature,snapshot.awaiting_plan,historical,active,connected,revision]);
 const current=!historical&&qualityResponseMatches(data,snapshot)?data:null;
 const forecast=current&&!snapshot.awaiting_plan&&snapshot.dispatch?.valid!==false?current.forecast:null;
 return <section className="panel quality-panel"><div className="panel-heading"><h2>Качество движения</h2><span className="subtle">{historical?'Архивный снимок':'Обновляется в эфире'}</span></div>
  {!historical&&<div className="quality-overview"><div><span>Текущий индекс</span><strong>{snapshot.metrics.index.toFixed(1)}</strong><small>Измерено к {clock(snapshot.sim_time_s)}</small></div><div><span>Прогноз по активному плану</span><strong>{forecast?.index.toFixed(1)??'—'}</strong><small>{snapshot.awaiting_plan||snapshot.dispatch?.valid===false?'Ожидает допустимого плана':forecast&&current?'Весь рейс · расчёт на '+clock(current.sim_time_s):'Загрузка прогноза'}</small></div><p>Прогноз оценивает весь сценарий. Текущий индекс учитывает выполненное движение и ограничения сейчас.</p></div>}
  {!historical&&<div className="quality-trend"><h3>Последние 15 минут модели</h3>{error?<p role="alert">{error} <Button size="small" onClick={()=>setRevision(n=>n+1)}>Повторить</Button></p>:<QualityChart points={qualityTrend(current,snapshot)}/>}<p>Только снимки этого запуска с текущими весами и формулой. При ускорении интервалы между точками больше.</p></div>}
  <QualityDetails metrics={snapshot.metrics} topology={topology}/>
  {forecast&&<details className="quality-forecast"><summary>Разбор прогноза</summary><QualityDetails metrics={forecast} topology={topology}/></details>}
 </section>;
}
