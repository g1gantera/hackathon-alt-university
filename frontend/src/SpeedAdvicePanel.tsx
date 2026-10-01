import {useEffect,useRef,useState} from 'react';
import {App as AntApp,Button,Tag} from 'antd';
import {api,clock,minutes} from './store';
import {SpeedChart} from './Charts';
import {advicePhase,currentProfile,currentProposal} from './speedAdvice';
import type {EcoResult,Profile,Snapshot,Topology,Train} from './types';

export function SpeedAdvicePanel({train,snapshot,topology,profile,historical,canControl,refresh}:{train:Train|undefined;snapshot:Snapshot;topology:Topology;profile:Profile|null;historical:boolean;canControl:boolean;refresh:()=>Promise<void>}) {
 const {message}=AntApp.useApp();
 const [proposal,setProposal]=useState<EcoResult|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0);
 const current=useRef({snapshot,trainId:train?.id,historical});
 current.current={snapshot,trainId:train?.id,historical};
 useEffect(()=>{generation.current++;setProposal(null);setError('');setBusy(false);return()=>{generation.current++;};},[train?.id,snapshot.epoch,snapshot.active_plan_id,snapshot.constraint_version,historical]);
 if(!train)return null;
 const advice=train.speed_advice;
 const activeProfile=currentProfile(profile,snapshot,train.id);
 const candidate=currentProposal(proposal,snapshot,train.id)?proposal:null;
 const economy=candidate?.plan?.eco;
 const ready=canControl&&!busy&&!snapshot.running&&!snapshot.awaiting_plan&&!snapshot.replanning&&snapshot.dispatch?.valid!==false;
 const station=(id:string|null)=>topology.stations.find(s=>s.id===id)?.name||'—';
 const section=(id:string)=>{const s=topology.sections.find(s=>s.id===id);return s?station(s.from_station)+' — '+station(s.to_station):id;};
 const calculate=async()=>{
  const version=++generation.current;
  setBusy(true);setError('');setProposal(null);
  try{
   const result=await api<EcoResult>('/trains/'+train.id+'/eco-plan','POST');
   if(version!==generation.current)return;
   if(current.current.historical||current.current.trainId!==train.id||!currentProposal(result,current.current.snapshot,train.id)){
    setError('Сценарий изменился. Повторите расчёт на паузе.');return;
   }
   setProposal(result);await refresh();
  }catch(e){if(version===generation.current)setError((e as Error).message);}
  finally{if(version===generation.current)setBusy(false);}
 };
 const apply=async()=>{
  if(!candidate?.plan)return;
  setBusy(true);setError('');
  try{await api('/plans/'+candidate.plan.id+'/apply','POST');setProposal(null);await refresh();message.success('Экономичный план применён. Нажмите «Запустить».');}
  catch(e){setError((e as Error).message);setProposal(null);}
  finally{setBusy(false);}
 };
 return <div className="speed-advice">
  {advice?<>
   <div className="advice-heading"><Tag color={advice.phase==='held'?'orange':'green'}>{advicePhase[advice.phase]}</Tag><span>{historical?'Архивный снимок':'Рекомендация по активному плану'}</span></div>
   <div className="advice-values"><div><span>Текущая скорость</span><strong>{(train.speed_mps*3.6).toFixed(1)} <small>км/ч</small></strong></div><div><span>Рекомендуемая сейчас</span><strong>{(advice.recommended_speed_mps*3.6).toFixed(1)} <small>км/ч</small></strong></div><div><span>Через {advice.lookahead_s} с модели</span><strong>{(advice.lookahead_speed_mps*3.6).toFixed(1)} <small>км/ч</small></strong></div></div>
   <dl className="quality-facts"><div><dt>Следующая станция</dt><dd>{station(advice.next_station_id)} · {advice.next_arrival_s!==null?clock(advice.next_arrival_s):'—'}</dd></div><div><dt>Прибытие на конечную</dt><dd>{advice.final_arrival_s!==null?clock(advice.final_arrival_s):'После перепланирования'}</dd></div><div><dt>Осталось энергии по плану</dt><dd>{advice.energy_remaining_kwh!==null?advice.energy_remaining_kwh.toFixed(1)+' кВт·ч':'Прогноз обновляется'}</dd></div></dl>
   {advice.reason==='clear_committed_section'&&<p className="advice-note">Завершить начатый перегон по сохранённому профилю. Дальнейший маршрут ждёт перепланирования.</p>}
   {advice.phase==='waiting'&&advice.departure_s!==null&&<p className="advice-note">Отправление: {clock(advice.departure_s)}. До этого рекомендуемая скорость — 0.</p>}
   {!!advice.energy_saving_kwh&&<p className="advice-note">Прогноз экономии активного рейса относительно быстрого профиля: {advice.energy_saving_kwh.toFixed(1)} кВт·ч ({advice.energy_saving_pct}%).</p>}
  </>:<p>В этом архивном снимке рекомендации скорости ещё не сохранялись.</p>}
  {historical?<p className="advice-note">Показаны сохранённые рекомендации. График и расчёт вариантов доступны в эфире.</p>:<>
   <SpeedChart profile={activeProfile} proposal={candidate?.profile||null} advice={advice}/>
   {activeProfile?.provisional&&<p className="advice-note">До нового плана показаны только начатые движения.</p>}
   <div className="eco-heading"><div><h3>Экономичный ход</h3><p>Снизить скорость за счёт лишней стоянки, сохранив конечное прибытие.</p></div><Button disabled={!ready} loading={busy} onClick={calculate}>Рассчитать</Button></div>
   {snapshot.running&&<p className="advice-note">Поставьте симуляцию на паузу для расчёта и сравнения.</p>}
   {error&&<p role="alert" className="advice-error">{error}</p>}
   {candidate?.reason==='no_safe_slack'&&<p className="advice-note">У этого поезда нет подходящего резерва времени. Сохраняйте текущий профиль.</p>}
   {candidate?.reason==='awaiting_valid_plan'&&<p className="advice-note">Сначала примените допустимый план движения.</p>}
   {proposal&&!candidate&&<p className="advice-note">Вариант устарел после изменения времени или условий. Рассчитайте его заново на паузе.</p>}
   {economy&&<div className="eco-proposal"><h3>Прогноз: −{economy.saving_kwh.toFixed(1)} кВт·ч <Tag color="green">{economy.saving_pct}%</Tag></h3><p>{economy.before_energy_kwh.toFixed(1)} → {economy.after_energy_kwh.toFixed(1)} кВт·ч за рейс. Конечное прибытие остаётся {clock(economy.final_arrival_s)}.</p><div className="dispatch-scroll"><table className="dispatch-table"><thead><tr><th>Перегон</th><th>Крейсерская скорость</th><th>Прибытие на станцию</th></tr></thead><tbody>{economy.changes.map(c=><tr key={c.leg}><td>{section(c.section_id)}</td><td>{(c.before_cruise_mps*3.6).toFixed(1)} → {(c.after_cruise_mps*3.6).toFixed(1)} км/ч</td><td>{clock(c.before_arrival_s)} → {clock(c.after_arrival_s)}<small>Стоянка короче на {minutes(c.extra_running_s)}</small></td></tr>)}</tbody></table></div><p>Промежуточные прибытия станут позже, а стоянки короче. Отправления и конечное прибытие сохранены. На отдельных интервалах пройденное расстояние будет меньше исходного.</p><Button type="primary" disabled={!ready} onClick={apply}>Применить экономичный план</Button></div>}
   <p className="advice-model">Расчётная тяговая энергия: ровный путь, без рекуперации. Экономия — прогноз модели. В симуляции скорость следует применённому профилю.</p>
  </>}
 </div>;
}
