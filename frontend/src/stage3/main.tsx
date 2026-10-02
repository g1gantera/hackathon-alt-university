import {useLanguage,initializeLanguage} from '../language';
import {LanguageSwitcher} from '../LanguageSwitcher';
import {ConfigProvider} from 'antd';
import ruRU from 'antd/locale/ru_RU';
import kkKZ from 'antd/locale/kk_KZ';
import enGB from 'antd/locale/en_GB';
import {translate, displayText} from '../i18n/core.ts';
import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { DemoData } from './model';
import { movementAt, timeLabel, trainPosition } from './model';
import './style.css';
import { SpeedControl } from '../SpeedControl';
const colors = ['#087f70', '#c67931', '#5267b4', '#b94d68', '#679146', '#9564a5', '#2794af', '#897748'];
const scenarios = [{ id: 'opposing', get title() {
            return translate("Встречные поезда");
        }, get text() {
            return translate("Пассажирский и грузовой идут навстречу друг другу.");
        } }, { id: 'following', get title() {
            return translate("Одна сторона");
        }, get text() {
            return translate("Два поезда запрашивают один перегон одновременно.");
        } }, { id: 'fleet', get title() {
            return translate("Весь участок");
        }, get text() {
            return translate("8 поездов, 5 перегонов, общее расписание.");
        } }];
const conflictNames: Record<string, string> = { get opposing() {
        return translate("Встречное движение");
    }, get following() {
        return translate("Попутное движение");
    }, get switch() {
        return translate("Занята стрелочная горловина");
    }, get capacity() {
        return translate("Превышена вместимость станции");
    } };
async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`/api/stage3/${path}`, { method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(20000), cache: 'no-store' });
    if (!response.ok) {
        const value = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(response.status === 401 ? translate("Войдите через основную диспетчерскую и вернитесь сюда.") : displayText(typeof value.detail === 'string' ? value.detail : value.detail?.message || JSON.stringify(value.detail)));
    }
    return response.json();
}
function ScheduleChart({ data, after, maxTime }: {
    data: DemoData;
    after: boolean;
    maxTime: number;
}) {
    const report = after ? data.after : data.before;
    if (!report)
        return <div className="lab-empty">{translate("Нажмите «Рассчитать безопасный план»")}</div>;
    const x = (s: number) => 105 + s / Math.max(1, maxTime) * 690;
    return <div className="lab-chart-scroll"><svg viewBox="0 0 820 263" role="img" aria-label={displayText(after ? translate("Порядок движения после расчёта") : translate("Конфликтующие заявки до расчёта"))}>
  {displayText(Array.from({ length: 5 }, (_, i) => <g key={i}><line x1={x(maxTime * i / 4)} x2={x(maxTime * i / 4)} y1={25} y2={231} stroke="#e4e9e5"/><text x={x(maxTime * i / 4)} y={251} textAnchor="middle" className="axis-text">{displayText(timeLabel(maxTime * i / 4).slice(0, 5))}</text></g>))}
  {displayText(report.section_order.map((group, index) => <g key={group.section_id}><text x={8} y={49 + index * 42} className="axis-text">{translate("Перегон ")}{displayText(index + 1)}</text><line x1={105} x2={795} y1={45 + index * 42} y2={45 + index * 42} stroke="#e4e9e5" strokeWidth={26}/>{displayText(group.reservations.map(m => {
                const i = data.trains.findIndex(t => t.id === m.train_id), train = data.trains[i];
                return <g key={`${m.train_id}-${m.leg}`}><rect x={x(m.start_s)} y={33 + index * 42 + (after ? 0 : i % 3 * 5)} width={Math.max(1, x(m.release_s) - x(m.start_s))} height={after ? 24 : 14} rx={3} fill={colors[i]} opacity={.85}><title>№ {displayText(train.number)}: {displayText(timeLabel(m.start_s))} → {displayText(timeLabel(m.end_s))}{translate("; освобождение ")}{displayText(timeLabel(m.release_s))}</title></rect>{displayText(after && x(m.release_s) - x(m.start_s) > 30 && <text x={x(m.start_s) + 4} y={49 + index * 42} fill="white" fontSize={10}>{displayText(train.number)}</text>)}</g>;
            }))}{displayText(!after && report.conflicts.filter(c => c.resource === group.section_id && c.start_s !== undefined).map((c, i) => <rect key={i} x={x(c.start_s!)} y={29 + index * 42} width={Math.max(3, x(c.end_s!) - x(c.start_s!))} height={32} fill="#f3535338" stroke="#b82d35" strokeWidth={2}/>))}</g>))}
 </svg></div>;
}
function Stage3() {
    useLanguage();
    const [scenario, setScenario] = useState('opposing'), [policy, setPolicy] = useState('balanced');
    const [data, setData] = useState<DemoData | null>(null), [priorities, setPriorities] = useState<Record<string, number>>({});
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0);
    const [time, setTime] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(300);
    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError('');
        setData(null);
        setPlaying(false);
        setTime(0);
        request<DemoData>(`scenario?scenario=${scenario}`).then(value => { if (!cancelled) {
            setData(value);
            setPriorities(Object.fromEntries(value.trains.map(t => [t.id, t.priority])));
        } }).catch(e => { if (!cancelled)
            setError(e.message); }).finally(() => { if (!cancelled)
            setLoading(false); });
        return () => { cancelled = true; };
    }, [scenario, reload]);
    const maxTime = Math.ceil(Math.max(1, ...(data?.after?.decisions || data?.before.decisions || []).map(m => m.release_s)));
    useEffect(() => {
        if (!playing)
            return;
        let previous = performance.now();
        const timer = setInterval(() => { const now = performance.now(); const delta = (now - previous) / 1000 * speed; previous = now; setTime(t => Math.min(maxTime, t + delta)); }, 100);
        return () => clearInterval(timer);
    }, [playing, speed, maxTime]);
    useEffect(() => { if (time >= maxTime)
        setPlaying(false); }, [time, maxTime]);
    const invalidate = () => { setPlaying(false); setTime(0); setError(''); setData(value => value ? { ...value, after: null, profiles: undefined } : null); };
    const calculate = async () => {
        setBusy(true);
        setPlaying(false);
        setTime(0);
        setError('');
        try {
            const result = await request<DemoData>('solve', { scenario, priorities, policy });
            setData(result);
            if (!result.after)
                setError(translate("Допустимый план не найден за отведённое время. Измените приоритеты и повторите расчёт."));
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setBusy(false);
        }
    };
    const sectionName = (id: string) => { const s = data?.sections.find(s => s.id === id); return s ? `${data?.stations.find(x => x.id === s.from_station)?.name} — ${data?.stations.find(x => x.id === s.to_station)?.name}` : id; };
    const number = (id: string) => data?.trains.find(t => t.id === id)?.number || id;
    return <div className="lab-app">
  <header className="lab-header"><LanguageSwitcher/><a className="lab-brand" href="/stage3.html"><span>↔</span> RailFlow <b>LAB</b></a><span className="lab-header-caption">{translate("Автодиспетчер · этап 03")}</span><a className="lab-back" href="/">{translate("Основная диспетчерская ↗")}</a></header>
  <main className="lab-main"><div className="lab-hero"><div><div className="lab-eyebrow">{translate("ПОНЯТЬ ЛОГИКУ ДВИЖЕНИЯ")}</div><h1>{translate("Кто проходит.")}<br /><em>{translate("Кто ждёт.")}</em></h1><p>{translate("Создайте конфликтующие заявки и посмотрите, как диспетчер строит согласованное расписание для участка Астана — Кокшетау.")}</p></div><div className="lab-isolated"><span className="lab-status-dot"/><strong>{translate("Отдельная песочница")}</strong><p>{translate("Ваши настройки и проигрывание работают только здесь. Основная симуляция продолжает жить отдельно.")}</p><small>{translate("Реальный планировщик · демонстрационные поезда")}</small></div></div>
   <div className="lab-layout"><aside className="lab-controls"><div className="lab-step">{translate("01 / ЗАДАЙТЕ УСЛОВИЯ")}</div><h2>{translate("Сценарий")}</h2><div className="lab-scenarios">{displayText(scenarios.map(s => <button key={s.id} disabled={busy} className={scenario === s.id ? 'selected' : ''} onClick={() => setScenario(s.id)} aria-pressed={scenario === s.id}><strong>{displayText(s.title)}</strong><span>{displayText(s.text)}</span></button>))}</div>
    <h2>{translate("Приоритеты поездов")}</h2><p className="lab-hint">{translate("Больше число — выше цена задержки. Безопасность пути всегда важнее приоритета.")}</p>
    <div className="lab-priorities">{displayText(data?.trains.map((t, i) => <label key={t.id}><span className="lab-train-dot" style={{ background: colors[i] }}/><span><strong>№ {displayText(t.number)} {displayText(t.direction === 1 ? '→' : '←')}</strong><small>{displayText(t.type === 'passenger' ? translate("Пассажирский") : translate("Грузовой"))}</small></span><input aria-label={displayText(translate("Приоритет поезда {0}", t.number))} type="number" min={1} max={10} value={priorities[t.id] ?? t.priority} disabled={busy} onChange={e => { setPriorities(p => ({ ...p, [t.id]: Math.min(10, Math.max(1, Math.round(Number(e.target.value)) || 1)) })); invalidate(); }}/></label>))}</div>
    <label className="lab-policy">{translate("Правило расчёта")}<select value={policy} disabled={busy} onChange={e => { setPolicy(e.target.value); invalidate(); }}><option value="balanced">{translate("Баланс задержек")}</option><option value="passenger_priority">{translate("Усилить приоритет пассажирских")}</option></select></label><p className="lab-hint">{translate("Базовый вес пассажирских — 3, грузовых — 1. Он умножается на заданный приоритет; усиленный режим добавляет ×3 пассажирским.")}</p>
    <button className="lab-primary" disabled={loading || busy || !data} onClick={calculate}>{displayText(busy ? translate("Ищем допустимый порядок…") : translate("Рассчитать безопасный план →"))}</button>
    <p className="lab-hint">{translate("Проверяем перегоны, стрелки, вместимость станций, стоянки и освобождение хвостом.")}</p>
   </aside><div className="lab-results">
    {displayText(error && <div className="lab-error" role="alert">{displayText(error)} {displayText(!data && <button onClick={() => setReload(v => v + 1)}>{translate("Повторить загрузку")}</button>)}</div>)}
    {displayText(loading ? <div className="lab-card lab-empty" role="status">{translate("Подготавливаем участок…")}</div> : data && <>
     <section className="lab-card"><div className="lab-step">{translate("02 / СРАВНИТЕ РАСПИСАНИЯ")}</div><h2>{translate("Один участок. Разный порядок.")}</h2><div className="lab-comparison"><article><div className="lab-comparison-heading"><span className="lab-pill danger">{translate("ДО РАСЧЁТА")}</span><strong>{displayText(data.before.conflicts.length)}<small>{translate("нарушений")}</small></strong></div><h3>{translate("Все отправляются по готовности")}</h3><p>{translate("Заявки не учитывают другие поезда. Красным отмечены пересечения резервирований.")}</p><ScheduleChart data={data} after={false} maxTime={maxTime}/></article><article><div className="lab-comparison-heading"><span className="lab-pill success">{translate("ПОСЛЕ РАСЧЁТА")}</span><strong>{displayText(data.after ? data.after.conflicts.length : '—')}<small>{translate("нарушений")}</small></strong></div><h3>{translate("Диспетчер назначает порядок")}</h3><p>{translate("Поезд ждёт на станции, пока путь и станционная горловина недоступны.")}</p><ScheduleChart data={data} after maxTime={maxTime}/></article></div>
      {displayText(data.after && <div className="lab-validation">{translate("✓ Независимая проверка пройдена ")}<span>{displayText(data.after.decisions.length)}{translate(" движений · ")}{displayText(data.calculation_s?.toFixed(2))}{translate(" с · ")}{displayText(data.solver_status === 'optimal' ? translate("Оптимум доказан") : data.solver_status === 'heuristic' ? translate("Резервная эвристика") : translate("Допустимый план"))}</span></div>)}
      <details className="lab-details"><summary>{translate("Какие конфликты найдены в заявках (")}{displayText(data.before.conflicts.length)})</summary><ul>{displayText(data.before.conflicts.map((c, i) => <li key={i}><b>{displayText(conflictNames[c.kind] || c.message)}</b> · {displayText(c.train_id && `№ ${number(c.train_id)}`)} {displayText(c.other_train_id && translate("и № {0}", number(c.other_train_id)))} {displayText(c.resource?.startsWith('section') && `· ${sectionName(c.resource)}`)} {displayText(c.start_s !== undefined && `· ${timeLabel(c.start_s)}–${timeLabel(c.end_s!)}`)}</li>))}</ul></details>
     </section>
     <section className="lab-card"><div className="lab-step">{translate("03 / ПОСМОТРИТЕ ДВИЖЕНИЕ")}</div><div className="lab-play-heading"><h2>{translate("Проигрыватель плана")}</h2><span className="lab-model-time">{displayText(timeLabel(time))}</span></div><p className="lab-hint">{translate("Каждая строка — положение одного поезда на том же участке. Проигрывание доступно после проверки расписания.")}</p><div className="lab-play-controls"><button className="lab-primary" disabled={!data.after || busy} onClick={() => { if (time >= maxTime)
            setTime(0); setPlaying(p => !p); }}>{displayText(playing ? translate("Ⅱ Пауза") : time >= maxTime ? translate("↻ Повторить") : translate("▶ Проиграть план"))}</button><button className="lab-secondary" disabled={!data.after || busy} onClick={() => { setPlaying(false); setTime(0); }}>{translate("В начало")}</button><label>{translate("Скорость")}<SpeedControl value={speed} onApply={setSpeed}/></label></div><input className="lab-slider" aria-label={translate("Время проигрывания расписания")} type="range" min={0} max={maxTime} value={Math.floor(time)} disabled={!data.after || busy} onChange={e => { setPlaying(false); setTime(Number(e.target.value)); }}/>
      <div className="lab-route-scroll"><svg viewBox={`0 0 960 ${75 + data.trains.length * 48}`} role="img" aria-label={translate("Положение поездов по расписанию")}>
       {displayText(data.stations.map((s, i) => <g key={s.id}><line x1={155 + s.position_m / data.length_m * 685} x2={155 + s.position_m / data.length_m * 685} y1={44} y2={60 + data.trains.length * 48} stroke="#d9e3dc" strokeDasharray="3 5"/><text x={155 + s.position_m / data.length_m * 685} y={i % 2 ? 35 : 19} textAnchor="middle" className="station-label">{displayText(s.name)}</text></g>))}
       {displayText(data.trains.map((t, i) => { const position = trainPosition(data, t, time), x = 155 + position.position / data.length_m * 685, y = 72 + i * 48; return <g key={t.id}><text x={4} y={y - 3} fontSize={13} fontWeight={600}>№ {displayText(t.number)}</text><text x={4} y={y + 12} className="axis-text">{displayText(position.status)}</text><line x1={155} x2={840} y1={y} y2={y} stroke="#d9e3dc" strokeWidth={3}/><circle cx={x} cy={y} r={12} fill={colors[i]}/><text x={x} y={y + 4} textAnchor="middle" fill="white" fontSize={15}>{displayText(t.direction === 1 ? '→' : '←')}</text></g>; }))}
      </svg></div>
     </section>
     {displayText(data.after && <section className="lab-card"><div className="lab-step">{translate("04 / РАЗБЕРИТЕ РЕШЕНИЯ")}</div><h2>{translate("Кому ждать и почему")}</h2><div className="lab-table-scroll"><table><thead><tr><th>{translate("Поезд")}</th><th>{translate("Сейчас")}</th><th>{translate("Следующий перегон")}</th><th>{translate("Отправление")}</th><th>{translate("Ожидание на станции")}</th><th>{translate("Предыдущие резервирования")}</th></tr></thead><tbody>{displayText(data.trains.map((t, i) => { const legs = data.after!.decisions.filter(m => m.train_id === t.id).sort((a, b) => a.leg - b.leg), d = movementAt(legs, time)!, decision = legs.find(m => m.leg === d.leg)!; const position = trainPosition(data, t, time); return <tr key={t.id}><td><b style={{ color: colors[i] }}>№ {displayText(t.number)}</b><small>{translate("Приоритет ")}{displayText(t.priority)}{translate(" · вес ×")}{displayText(decision.lateness_weight)}</small></td><td>{displayText(position.status)}</td><td>{displayText(position.status === translate("Прибыл") ? translate("Маршрут завершён") : sectionName(d.section_id))}</td><td>{displayText(timeLabel(d.start_s))}</td><td>{displayText((decision.wait_s / 60).toFixed(1))}{translate(" мин")}<small>{displayText(time < d.start_s ? translate("Осталось {0} мин", ((d.start_s - time) / 60).toFixed(1)) : translate("Ожидание завершено"))}</small></td><td>{displayText(decision.predecessors.length ? [...new Set(decision.predecessors.map(p => `№ ${number(p.train_id)}`))].join(', ') : translate("Готовность и согласование расписания"))}</td></tr>; }))}</tbody></table></div><p className="lab-hint">{translate("Ожидание отсчитывается после готовности и обязательной стоянки. Предыдущие резервирования объясняют порядок на общих ресурсах; приоритет — один из факторов общего расчёта.")}</p></section>)}
    </>)}
   </div></div><footer className="lab-footer">{translate("RailFlow Lab · учебный сценарий Астана — Кокшетау ")}<span>{translate("Этап 3: конфликты → приоритеты → расписание")}</span></footer>
  </main>
 </div>;
}
function LocalizedStage3(){const language=useLanguage();return <ConfigProvider locale={{ru:ruRU,kk:kkKZ,en:enGB}[language]}><Stage3/></ConfigProvider>;}
initializeLanguage();
createRoot(document.getElementById('root')!).render(<React.StrictMode><LocalizedStage3 /></React.StrictMode>);
