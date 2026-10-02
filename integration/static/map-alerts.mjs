export function trainNotice(train, forecastValid=true) {
  const parts=[];
  if(train.holding)parts.push('Остановка на перегоне');
  if(train.delay_s>0)parts.push(`Опоздание +${Math.ceil(train.delay_s/60)} мин`);
  else if(forecastValid&&train.due_s!=null&&train.eta_s!=null&&train.eta_s>train.due_s)parts.push(`Прогноз +${Math.ceil((train.eta_s-train.due_s)/60)} мин`);
  if(train.waiting_for?.length)parts.push(`Пропускает ${train.waiting_for.join(', ')}`);
  return {text:parts.join(' · '),level:train.holding||train.delay_s>0?'danger':parts.length?'warning':'normal'};
}
export function incidentNotices(snapshot,topology){
 const names={closure:'Закрытие пути',signal:'Неисправность сигнала',speed_restriction:'Ограничение скорости',delay:'Задержка поезда'};
 const items=[];
 for(const event of snapshot.incidents){
  if(event.end_s<=snapshot.sim_time_s)continue;
  const resources=event.constraint_intervals?.length?event.constraint_intervals.map(i=>({...i,target_id:i.resource})): [event];
  for(const [index,item] of resources.entries()){
   if(item.end_s<=snapshot.sim_time_s)continue;
   const target=item.target_id||event.target_id;
   const section=topology.sections.find(s=>target===s.id||target===`section:${s.id}`||target.startsWith(`main_track:${s.id}:`));
   const station=topology.stations.find(s=>target===s.id||target.startsWith(`track:${s.id}:`)||target.startsWith(`switch:${s.id}:`));
   const train=snapshot.trains.find(t=>t.id===target);
   let coordinate=station?.coordinate;
   if(section)coordinate=section.geometry[Math.floor(section.geometry.length/2)];
   if(train?.station_id)coordinate=topology.stations.find(s=>s.id===train.station_id)?.coordinate;
   items.push({id:`${event.id}:${index}`,coordinate,train_id:train?.id,kind:item.kind||event.kind,text:`${item.start_s>snapshot.sim_time_s?'Предстоит: ':''}${names[item.kind]||names[event.kind]||'Сбой на участке'}`,end_s:item.end_s,target});
  }
 }
 return items;
}
