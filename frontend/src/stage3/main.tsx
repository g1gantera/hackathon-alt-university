import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import type {DemoData} from './model';
import {movementAt,timeLabel,trainPosition} from './model';
import './style.css';
import {SpeedControl} from '../SpeedControl';

const colors=['#087f70','#c67931','#5267b4','#b94d68','#679146','#9564a5','#2794af','#897748'];
const scenarios=[{id:'opposing',title:'Встречные поезда',text:'Пассажирский и грузовой идут навстречу друг другу.'},{id:'following',title:'Одна сторона',text:'Два поезда запрашивают один перегон одновременно.'},{id:'fleet',title:'Весь участок',text:'8 поездов, 5 перегонов, общее расписание.'}];
const conflictNames:Record<string,string>={opposing:'Встречное движение',following:'Попутное движение',switch:'Занята стрелочная горловина',capacity:'Превышена вместимость станции'};

async function request<T>(path:string,body?:unknown):Promise<T>{
 const response=await fetch(`/api/stage3/${path}`,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(20000),cache:'no-store'});
 if(!response.ok){const value=await response.json().catch(()=>({detail:response.statusText}));throw new Error(response.status===401?'Войдите через основную диспетчерскую и вернитесь сюда.':typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));}
 return response.json();
}

function ScheduleChart({data,after,maxTime}:{data:DemoData;after:boolean;maxTime:number}){
 const report=after?data.after:data.before;
 if(!report)return <div className="lab-empty">Нажмите «Рассчитать безопасный план»</div>;
 const x=(s:number)=>105+s/Math.max(1,maxTime)*690;
 return <div className="lab-chart-scroll"><svg viewBox="0 0 820 263" role="img" aria-label={after?'Порядок движения после расчёта':'Конфликтующие заявки до расчёта'}>
  {Array.from({length:5},(_,i)=><g key={i}><line x1={x(maxTime*i/4)} x2={x(maxTime*i/4)} y1={25} y2={231} stroke="#e4e9e5"/><text x={x(maxTime*i/4)} y={251} textAnchor="middle" className="axis-text">{timeLabel(maxTime*i/4).slice(0,5)}</text></g>)}
  {report.section_order.map((group,index)=><g key={group.section_id}><text x={8} y={49+index*42} className="axis-text">Перегон {index+1}</text><line x1={105} x2={795} y1={45+index*42} y2={45+index*42} stroke="#e4e9e5" strokeWidth={26}/>{group.reservations.map(m=>{
   const i=data.trains.findIndex(t=>t.id===m.train_id),train=data.trains[i];
   return <g key={`${m.train_id}-${m.leg}`}><rect x={x(m.start_s)} y={33+index*42+(after?0:i%3*5)} width={Math.max(1,x(m.release_s)-x(m.start_s))} height={after?24:14} rx={3} fill={colors[i]} opacity={.85}><title>№ {train.number}: {timeLabel(m.start_s)} → {timeLabel(m.end_s)}; освобождение {timeLabel(m.release_s)}</title></rect>{after&&x(m.release_s)-x(m.start_s)>30&&<text x={x(m.start_s)+4} y={49+index*42} fill="white" fontSize={10}>{train.number}</text>}</g>;
  })}{!after&&report.conflicts.filter(c=>c.resource===group.section_id&&c.start_s!==undefined).map((c,i)=><rect key={i} x={x(c.start_s!)} y={29+index*42} width={Math.max(3,x(c.end_s!)-x(c.start_s!))} height={32} fill="#f3535338" stroke="#b82d35" strokeWidth={2}/>)}</g>)}
 </svg></div>;
}

function Stage3(){
 const [scenario,setScenario]=useState('opposing'),[policy,setPolicy]=useState('balanced');
 const [data,setData]=useState<DemoData|null>(null),[priorities,setPriorities]=useState<Record<string,number>>({});
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0);
 const [time,setTime]=useState(0),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(300);
 useEffect(()=>{
  let cancelled=false;
  setLoading(true);setError('');setData(null);setPlaying(false);setTime(0);
  request<DemoData>(`scenario?scenario=${scenario}`).then(value=>{if(!cancelled){setData(value);setPriorities(Object.fromEntries(value.trains.map(t=>[t.id,t.priority])));}}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});
  return()=>{cancelled=true;};
 },[scenario,reload]);
 const maxTime=Math.ceil(Math.max(1,...(data?.after?.decisions||data?.before.decisions||[]).map(m=>m.release_s)));
 useEffect(()=>{
  if(!playing)return;
  let previous=performance.now();
  const timer=setInterval(()=>{const now=performance.now();const delta=(now-previous)/1000*speed;previous=now;setTime(t=>Math.min(maxTime,t+delta));},100);
  return()=>clearInterval(timer);
 },[playing,speed,maxTime]);
 useEffect(()=>{if(time>=maxTime)setPlaying(false);},[time,maxTime]);
 const invalidate=()=>{setPlaying(false);setTime(0);setError('');setData(value=>value?{...value,after:null,profiles:undefined}:null);};
 const calculate=async()=>{
  setBusy(true);setPlaying(false);setTime(0);setError('');
  try{const result=await request<DemoData>('solve',{scenario,priorities,policy});setData(result);if(!result.after)setError('Допустимый план не найден за отведённое время. Измените приоритеты и повторите расчёт.');}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 };
 const sectionName=(id:string)=>{const s=data?.sections.find(s=>s.id===id);return s?`${data?.stations.find(x=>x.id===s.from_station)?.name} — ${data?.stations.find(x=>x.id===s.to_station)?.name}`:id;};
 const number=(id:string)=>data?.trains.find(t=>t.id===id)?.number||id;
 return <div className="lab-app">
  <header className="lab-header"><a className="lab-brand" href="/stage3.html"><span>↔</span> RailFlow <b>LAB</b></a><span className="lab-header-caption">Автодиспетчер · этап 03</span><a className="lab-back" href="/">Основная диспетчерская ↗</a></header>
  <main className="lab-main"><div className="lab-hero"><div><div className="lab-eyebrow">ПОНЯТЬ ЛОГИКУ ДВИЖЕНИЯ</div><h1>Кто проходит.<br/><em>Кто ждёт.</em></h1><p>Создайте конфликтующие заявки и посмотрите, как диспетчер строит согласованное расписание для участка Астана — Кокшетау.</p></div><div className="lab-isolated"><span className="lab-status-dot"/><strong>Отдельная песочница</strong><p>Ваши настройки и проигрывание работают только здесь. Основная симуляция продолжает жить отдельно.</p><small>Реальный планировщик · демонстрационные поезда</small></div></div>
   <div className="lab-layout"><aside className="lab-controls"><div className="lab-step">01 / ЗАДАЙТЕ УСЛОВИЯ</div><h2>Сценарий</h2><div className="lab-scenarios">{scenarios.map(s=><button key={s.id} disabled={busy} className={scenario===s.id?'selected':''} onClick={()=>setScenario(s.id)} aria-pressed={scenario===s.id}><strong>{s.title}</strong><span>{s.text}</span></button>)}</div>
    <h2>Приоритеты поездов</h2><p className="lab-hint">Больше число — выше цена задержки. Безопасность пути всегда важнее приоритета.</p>
    <div className="lab-priorities">{data?.trains.map((t,i)=><label key={t.id}><span className="lab-train-dot" style={{background:colors[i]}}/><span><strong>№ {t.number} {t.direction===1?'→':'←'}</strong><small>{t.type==='passenger'?'Пассажирский':'Грузовой'}</small></span><input aria-label={`Приоритет поезда ${t.number}`} type="number" min={1} max={10} value={priorities[t.id]??t.priority} disabled={busy} onChange={e=>{setPriorities(p=>({...p,[t.id]:Math.min(10,Math.max(1,Math.round(Number(e.target.value))||1))}));invalidate();}}/></label>)}</div>
    <label className="lab-policy">Правило расчёта<select value={policy} disabled={busy} onChange={e=>{setPolicy(e.target.value);invalidate();}}><option value="balanced">Баланс задержек</option><option value="passenger_priority">Усилить приоритет пассажирских</option></select></label><p className="lab-hint">Базовый вес пассажирских — 3, грузовых — 1. Он умножается на заданный приоритет; усиленный режим добавляет ×3 пассажирским.</p>
    <button className="lab-primary" disabled={loading||busy||!data} onClick={calculate}>{busy?'Ищем допустимый порядок…':'Рассчитать безопасный план →'}</button>
    <p className="lab-hint">Проверяем перегоны, стрелки, вместимость станций, стоянки и освобождение хвостом.</p>
   </aside><div className="lab-results">
    {error&&<div className="lab-error" role="alert">{error} {!data&&<button onClick={()=>setReload(v=>v+1)}>Повторить загрузку</button>}</div>}
    {loading?<div className="lab-card lab-empty" role="status">Подготавливаем участок…</div>:data&&<>
     <section className="lab-card"><div className="lab-step">02 / СРАВНИТЕ РАСПИСАНИЯ</div><h2>Один участок. Разный порядок.</h2><div className="lab-comparison"><article><div className="lab-comparison-heading"><span className="lab-pill danger">ДО РАСЧЁТА</span><strong>{data.before.conflicts.length}<small>нарушений</small></strong></div><h3>Все отправляются по готовности</h3><p>Заявки не учитывают другие поезда. Красным отмечены пересечения резервирований.</p><ScheduleChart data={data} after={false} maxTime={maxTime}/></article><article><div className="lab-comparison-heading"><span className="lab-pill success">ПОСЛЕ РАСЧЁТА</span><strong>{data.after?data.after.conflicts.length:'—'}<small>нарушений</small></strong></div><h3>Диспетчер назначает порядок</h3><p>Поезд ждёт на станции, пока путь и станционная горловина недоступны.</p><ScheduleChart data={data} after maxTime={maxTime}/></article></div>
      {data.after&&<div className="lab-validation">✓ Независимая проверка пройдена <span>{data.after.decisions.length} движений · {data.calculation_s?.toFixed(2)} с · {data.solver_status==='optimal'?'Оптимум доказан':data.solver_status==='heuristic'?'Резервная эвристика':'Допустимый план'}</span></div>}
      <details className="lab-details"><summary>Какие конфликты найдены в заявках ({data.before.conflicts.length})</summary><ul>{data.before.conflicts.map((c,i)=><li key={i}><b>{conflictNames[c.kind]||c.message}</b> · {c.train_id&&`№ ${number(c.train_id)}`} {c.other_train_id&&`и № ${number(c.other_train_id)}`} {c.resource?.startsWith('section')&&`· ${sectionName(c.resource)}`} {c.start_s!==undefined&&`· ${timeLabel(c.start_s)}–${timeLabel(c.end_s!)}`}</li>)}</ul></details>
     </section>
     <section className="lab-card"><div className="lab-step">03 / ПОСМОТРИТЕ ДВИЖЕНИЕ</div><div className="lab-play-heading"><h2>Проигрыватель плана</h2><span className="lab-model-time">{timeLabel(time)}</span></div><p className="lab-hint">Каждая строка — положение одного поезда на том же участке. Проигрывание доступно после проверки расписания.</p><div className="lab-play-controls"><button className="lab-primary" disabled={!data.after||busy} onClick={()=>{if(time>=maxTime)setTime(0);setPlaying(p=>!p);}}>{playing?'Ⅱ Пауза':time>=maxTime?'↻ Повторить':'▶ Проиграть план'}</button><button className="lab-secondary" disabled={!data.after||busy} onClick={()=>{setPlaying(false);setTime(0);}}>В начало</button><label>Скорость<SpeedControl value={speed} onApply={setSpeed}/></label></div><input className="lab-slider" aria-label="Время проигрывания расписания" type="range" min={0} max={maxTime} value={Math.floor(time)} disabled={!data.after||busy} onChange={e=>{setPlaying(false);setTime(Number(e.target.value));}}/>
      <div className="lab-route-scroll"><svg viewBox={`0 0 960 ${75+data.trains.length*48}`} role="img" aria-label="Положение поездов по расписанию">
       {data.stations.map((s,i)=><g key={s.id}><line x1={155+s.position_m/data.length_m*685} x2={155+s.position_m/data.length_m*685} y1={44} y2={60+data.trains.length*48} stroke="#d9e3dc" strokeDasharray="3 5"/><text x={155+s.position_m/data.length_m*685} y={i%2?35:19} textAnchor="middle" className="station-label">{s.name}</text></g>)}
       {data.trains.map((t,i)=>{const position=trainPosition(data,t,time),x=155+position.position/data.length_m*685,y=72+i*48;return <g key={t.id}><text x={4} y={y-3} fontSize={13} fontWeight={600}>№ {t.number}</text><text x={4} y={y+12} className="axis-text">{position.status}</text><line x1={155} x2={840} y1={y} y2={y} stroke="#d9e3dc" strokeWidth={3}/><circle cx={x} cy={y} r={12} fill={colors[i]}/><text x={x} y={y+4} textAnchor="middle" fill="white" fontSize={15}>{t.direction===1?'→':'←'}</text></g>;})}
      </svg></div>
     </section>
     {data.after&&<section className="lab-card"><div className="lab-step">04 / РАЗБЕРИТЕ РЕШЕНИЯ</div><h2>Кому ждать и почему</h2><div className="lab-table-scroll"><table><thead><tr><th>Поезд</th><th>Сейчас</th><th>Следующий перегон</th><th>Отправление</th><th>Ожидание на станции</th><th>Предыдущие резервирования</th></tr></thead><tbody>{data.trains.map((t,i)=>{const legs=data.after!.decisions.filter(m=>m.train_id===t.id).sort((a,b)=>a.leg-b.leg),d=movementAt(legs,time)!,decision=legs.find(m=>m.leg===d.leg)!;const position=trainPosition(data,t,time);return <tr key={t.id}><td><b style={{color:colors[i]}}>№ {t.number}</b><small>Приоритет {t.priority} · вес ×{decision.lateness_weight}</small></td><td>{position.status}</td><td>{position.status==='Прибыл'?'Маршрут завершён':sectionName(d.section_id)}</td><td>{timeLabel(d.start_s)}</td><td>{(decision.wait_s/60).toFixed(1)} мин<small>{time<d.start_s?`Осталось ${((d.start_s-time)/60).toFixed(1)} мин`:'Ожидание завершено'}</small></td><td>{decision.predecessors.length?[...new Set(decision.predecessors.map(p=>`№ ${number(p.train_id)}`))].join(', '):'Готовность и согласование расписания'}</td></tr>;})}</tbody></table></div><p className="lab-hint">Ожидание отсчитывается после готовности и обязательной стоянки. Предыдущие резервирования объясняют порядок на общих ресурсах; приоритет — один из факторов общего расчёта.</p></section>}
    </>}
   </div></div><footer className="lab-footer">RailFlow Lab · учебный сценарий Астана — Кокшетау <span>Этап 3: конфликты → приоритеты → расписание</span></footer>
  </main>
 </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><Stage3/></React.StrictMode>);
