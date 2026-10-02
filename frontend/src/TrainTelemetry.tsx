import {translate, displayText, intlLocale} from './i18n/core.ts';
import type { Snapshot, Train, Topology } from './types';
import { clock } from './store';
import { advicePhase } from './speedAdvice';
export function TrainTelemetry({ train, snapshot, historical, topology }: {
    train: Train | undefined;
    snapshot: Snapshot;
    historical: boolean;
    topology: Topology;
}) {
    if (!train)
        return null;
    const station = (id: string) => topology.stations.find(s => s.id === id)?.name || id;
    const section = topology.sections.find(s => s.id === train.section_id);
    return <div className="telemetry" aria-label={translate("Данные поезда")}>
  <div className="telemetry-route">{displayText(train.route_name)}</div>
  <div className="detail-grid">
   <div><span>{translate("Текущая скорость")}</span><strong>{displayText((train.speed_mps * 3.6).toFixed(1))}{translate(" км/ч")}</strong></div>
   <div><span>{translate("Задержка")}</span><strong>{displayText((train.delay_s / 60).toFixed(1))}{translate(" мин")}</strong></div>
   <div><span>{translate("Прибытие по плану")}</span><strong>{displayText(train.eta_s === null ? translate("Ожидает плана") : clock(train.eta_s))}</strong></div>
   <div><span>{translate("Станция / перегон")}</span><strong>{displayText(section ? station(section.from_station) + ' — ' + station(section.to_station) : train.station_id ? station(train.station_id) : '—')}</strong></div>
  </div>
  {displayText(train.speed_advice && <div className="telemetry-advice"><strong>{displayText(advicePhase[train.speed_advice.phase])}</strong><span>{translate("Рекомендуется ")}{displayText((train.speed_advice.recommended_speed_mps * 3.6).toFixed(1))}{translate(" км/ч")}</span><span>{translate("График и экономичный вариант — в «Аналитике»")}</span></div>)}
  <details className="telemetry-source"><summary>{translate("Подробности поезда")}</summary><p>{displayText(train.mass_kg / 1000)}{translate(" т · ")}{displayText(train.length_m)}{translate(" м · ")}{displayText(train.coordinate[1].toFixed(5))}, {displayText(train.coordinate[0].toFixed(5))}</p><p>{displayText(historical ? translate("Архив") : translate("Моделируемое движение"))}{displayText(snapshot.realtime && ' · ' + new Date(snapshot.realtime.observed_at).toLocaleTimeString(intlLocale()))}</p></details>
 </div>;
}
