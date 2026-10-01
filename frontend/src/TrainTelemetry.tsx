import type {Snapshot,Train,Topology} from './types';
import {clock} from './store';
import {advicePhase} from './speedAdvice';

export function TrainTelemetry({train,snapshot,historical,topology}:{train:Train|undefined;snapshot:Snapshot;historical:boolean;topology:Topology}) {
 if(!train)return null;
 const station=(id:string)=>topology.stations.find(s=>s.id===id)?.name||id;
 const section=topology.sections.find(s=>s.id===train.section_id);
 return <div className="telemetry" aria-label="Данные поезда">
  <div className="telemetry-route">{train.route_name}</div>
  <div className="detail-grid">
   <div><span>Текущая скорость</span><strong>{(train.speed_mps*3.6).toFixed(1)} км/ч</strong></div>
   <div><span>Задержка</span><strong>{(train.delay_s/60).toFixed(1)} мин</strong></div>
   <div><span>Прибытие по плану</span><strong>{train.eta_s===null?'Ожидает плана':clock(train.eta_s)}</strong></div>
   <div><span>Станция / перегон</span><strong>{section?station(section.from_station)+' — '+station(section.to_station):train.station_id?station(train.station_id):'—'}</strong></div>
  </div>
  {train.speed_advice&&<div className="telemetry-advice"><strong>{advicePhase[train.speed_advice.phase]}</strong><span>Рекомендуется {(train.speed_advice.recommended_speed_mps*3.6).toFixed(1)} км/ч</span><span>График и экономичный вариант — в «Аналитике»</span></div>}
  <details className="telemetry-source"><summary>Подробности поезда</summary><p>{train.mass_kg/1000} т · {train.length_m} м · {train.coordinate[1].toFixed(5)}, {train.coordinate[0].toFixed(5)}</p><p>{historical?'Архив':'Моделируемое движение'}{snapshot.realtime&&' · '+new Date(snapshot.realtime.observed_at).toLocaleTimeString('ru')}</p></details>
 </div>;
}
