import {translate, displayText, intlLocale} from './i18n/core.ts';
import { useEffect, useRef, useState } from 'react';
import { Button, Drawer, Select, Slider, Spin } from 'antd';
import { Download, Pause, Play, SkipBack, SkipForward } from 'lucide-react';
import { api, clock } from './store';
import { eventFrame, eventLabel, matchesFrame, reportUrl } from './history';
import type { HistoryRun, HistoryWindow } from './history';
import type { Snapshot, Topology } from './types';
import './history.css';
interface Props {
    live: Snapshot;
    topology: Topology;
    open: boolean;
    onClose: () => void;
    onView: (snapshot: Snapshot | null) => void;
}
export function HistoryPanel({ live, topology, open, onClose, onView }: Props) {
    const [runs, setRuns] = useState<HistoryRun[]>([]), [run, setRun] = useState<string | null>(null);
    const [minutes, setMinutes] = useState<5 | 10 | 15>(15), [refresh, setRefresh] = useState(0);
    const [window, setWindow] = useState<HistoryWindow | null>(null), [index, setIndex] = useState<number | null>(null);
    const [playing, setPlaying] = useState(false), [rate, setRate] = useState(1), [loading, setLoading] = useState(false);
    const [frameLoading, setFrameLoading] = useState(false), [frameError, setFrameError] = useState(''), [error, setError] = useState('');
    const [shown, setShown] = useState<Snapshot | null>(null);
    const cache = useRef(new Map<number, Snapshot>());
    const loaded = useRef<{
        key: string;
        window: HistoryWindow;
    } | null>(null);
    const epoch = run ?? live.epoch;
    const frame = index === null ? undefined : window?.frames[index];
    const ready = !!(window && frame && shown && matchesFrame(shown, window.epoch, frame));
    useEffect(() => {
        if (!open)
            return;
        let cancelled = false;
        api<{
            runs: HistoryRun[];
        }>('/history/runs').then(data => { if (!cancelled)
            setRuns(data.runs); }).catch(() => { });
        return () => { cancelled = true; };
    }, [open, live.epoch, refresh]);
    useEffect(() => {
        if (!open)
            return;
        const key = `${epoch}:${minutes}:${refresh}`;
        // Reopening the drawer must keep the selected frame and fixed report window.
        if (loaded.current?.key === key) {
            const frames = loaded.current.window.frames;
            setIndex(current => current ?? (frames.length ? frames.length - 1 : null));
            return;
        }
        let cancelled = false;
        loaded.current = null;
        setLoading(true);
        setError('');
        setPlaying(false);
        setIndex(null);
        setShown(null);
        setWindow(null);
        onView(null);
        cache.current.clear();
        api<HistoryWindow>(`/history/window?epoch=${encodeURIComponent(epoch)}&minutes=${minutes}`).then(data => {
            if (cancelled)
                return;
            loaded.current = { key, window: data };
            setWindow(data);
            setIndex(data.frames.length ? data.frames.length - 1 : null);
        }).catch(e => { if (!cancelled)
            setError((e as Error).message); }).finally(() => { if (!cancelled)
            setLoading(false); });
        return () => { cancelled = true; };
    }, [open, epoch, minutes, refresh, onView]);
    useEffect(() => {
        if (!window || !frame) {
            setFrameLoading(false);
            return;
        }
        let cancelled = false;
        setFrameLoading(true);
        setFrameError('');
        const load = async () => {
            try {
                const snapshot = cache.current.get(frame.id) ?? await api<Snapshot>(`/history/snapshots/${frame.id}?epoch=${encodeURIComponent(window.epoch)}`);
                if (cancelled)
                    return;
                if (!matchesFrame(snapshot, window.epoch, frame))
                    throw new Error(translate("Снимок не соответствует выбранному запуску. Обновите историю."));
                cache.current.set(frame.id, snapshot);
                if (cache.current.size > 12)
                    cache.current.delete(cache.current.keys().next().value!);
                setShown(snapshot);
                onView(snapshot);
            }
            catch (e) {
                if (!cancelled) {
                    setPlaying(false);
                    setFrameError((e as Error).message);
                }
            }
            finally {
                if (!cancelled)
                    setFrameLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [window, frame, onView]);
    useEffect(() => {
        if (!playing || !ready || frameLoading || !window || index === null)
            return;
        if (index >= window.frames.length - 1) {
            setPlaying(false);
            return;
        }
        const timer = setTimeout(() => setIndex(index + 1), 1000 / rate);
        return () => clearTimeout(timer);
    }, [playing, ready, frameLoading, window, index, rate]);
    const seek = (next: number) => { setPlaying(false); setIndex(next); };
    const backToLive = () => { setPlaying(false); setIndex(null); setShown(null); setFrameError(''); onView(null); onClose(); };
    const togglePlay = () => {
        if (playing) {
            setPlaying(false);
            return;
        }
        if (!window?.frames.length)
            return;
        if (index === null || index === window.frames.length - 1)
            setIndex(0);
        setPlaying(true);
    };
    const controls = <div className="archive-controls">
  <Button aria-label={translate("Предыдущий снимок")} icon={<SkipBack size={15}/>} disabled={!window?.frames.length || index === 0 || index === null} onClick={() => seek(index! - 1)}/>
  <Button aria-label={displayText(playing ? translate("Пауза архива") : translate("Воспроизвести архив"))} icon={playing ? <Pause size={15}/> : <Play size={15}/>} disabled={!window || window.frames.length < 2 || !!frameError} onClick={togglePlay}>{displayText(playing ? translate("Пауза") : translate("Воспроизвести"))}</Button>
  <Button aria-label={translate("Следующий снимок")} icon={<SkipForward size={15}/>} disabled={!window || index === null || index >= window.frames.length - 1} onClick={() => seek(index! + 1)}/>
  <Select aria-label={translate("Скорость просмотра архива")} value={rate} onChange={setRate} options={[1, 2, 4].map(value => ({ value, label: translate("{0} кадр/с", value) }))}/>
 </div>;
    const timeline = window && window.frames.length > 0 ? <>
  <Slider aria-label={translate("Сохранённый момент времени")} min={0} max={Math.max(1, window.frames.length - 1)} disabled={window.frames.length < 2} value={index ?? window.frames.length - 1} onChange={seek} tooltip={{ formatter: value => clock(window.frames[value ?? 0]?.sim_time_s ?? 0) }}/>
  <div className="archive-range"><span>{displayText(clock(window.frames[0].sim_time_s))}</span><strong>{displayText(frameLoading ? <Spin size="small"/> : null)} {displayText(shown ? clock(shown.sim_time_s) : translate("Выберите снимок"))}</strong><span>{displayText(clock(window.frames.at(-1)!.sim_time_s))}</span></div>
 </> : null;
    const download = window && window.frames.length > 0 ? <Button href={reportUrl(window)} icon={<Download size={15}/>} download>{translate("Скачать отчёт CSV")}</Button> : null;
    const targetName = (id: string) => {
        const section = topology.sections.find(item => item.id === id);
        if (section)
            return [section.from_station, section.to_station].map(station => topology.stations.find(item => item.id === station)?.name || station).join(' — ');
        const train = (shown ?? live).trains.find(item => item.id === id);
        return train ? translate("Поезд № {0}", train.number) : id;
    };
    return <>
  <Drawer title={translate("История и отчёт")} open={open} onClose={onClose} width={520}>
   <div className="archive-fields">
    <label>{translate("Сохранённый запуск")}<Select aria-label={translate("Сохранённый запуск")} value={epoch} onChange={value => setRun(value)} options={runs.map(item => ({ value: item.epoch, label: translate("{0}{1} · до {2}", item.epoch === live.epoch ? translate("Текущий · ") : '', new Date(item.saved_at * 1000).toLocaleString(intlLocale()), clock(item.last_time_s)) }))}/></label>
    <div className="archive-actions"><Select aria-label={translate("Глубина истории")} value={minutes} onChange={setMinutes} options={[5, 10, 15].map(value => ({ value, label: translate("Последние {0} минут", value) }))}/><Button onClick={() => setRefresh(value => value + 1)} loading={loading}>{translate("Обновить")}</Button></div>
   </div>
   <p className="subtle">{translate("Время модели. Просмотр не меняет симуляцию. Снимки и отчёт фиксируются при загрузке; новые данные появятся после обновления.")}</p>
   {displayText(loading ? <div className="empty"><Spin /></div> : error ? <p role="alert" className="archive-error">{displayText(error)}</p> : window && window.frames.length ? <>
    {displayText(timeline)}{displayText(controls)}
    <p className="subtle">{translate("Кадр ")}{displayText(index === null ? '—' : index + 1)}{translate(" из ")}{displayText(window.frames.length)} · {displayText(window.events.length)}{translate(" событий. Воспроизводятся сохранённые кадры: при ускорении модели между ними возможны пропуски.")}</p>
    {displayText(frameError && <p role="alert" className="archive-error">{displayText(frameError)}</p>)}
    <div className="archive-actions">{displayText(download)}<Button type="primary" onClick={backToLive}>{translate("Вернуться в эфир")}</Button></div>
    <p className="subtle">{translate("CSV: состояния поездов, задержки, качество, нарушения расписания, события и прогнозы до/после применения плана. Фильтруйте строки по record_type.")}</p>
    <h3>{translate("События в этом окне")}</h3>
    <div className="archive-events">{displayText(window.events.length ? window.events.map(event => <button key={event.id} onClick={() => seek(eventFrame(window.frames, event))}>
     <time>{displayText(clock(event.sim_time_s))}</time><span>{displayText(eventLabel(event))}{displayText(event.target_id && <small>{displayText(targetName(event.target_id))}</small>)}</span>
    </button>) : <p className="subtle">{translate("В этом окне событий нет.")}</p>)}</div>
    <p className="subtle">{translate("Хранение: ")}{displayText(window.retention_hours)}{translate(" ч реального времени. Сброс создаёт отдельный запуск.")}</p>
   </> : <p className="empty">{translate("Сохранённых снимков нет.")}</p>)}
  </Drawer>
  {displayText(!open && shown && window && index !== null && <section className="archive-player" aria-label={translate("Просмотр архива")}>
   <div className="archive-player-heading"><strong>{translate("АРХИВ · ")}{displayText(clock(shown.sim_time_s))}</strong><span>{displayText(window.epoch === live.epoch ? translate("Текущий запуск") : translate("Предыдущий запуск"))}{translate(" · кадр ")}{displayText(index + 1)}/{displayText(window.frames.length)}</span><Button type="primary" onClick={backToLive}>{translate("В эфир")}</Button></div>
   {displayText(timeline)}<div className="archive-player-actions">{displayText(controls)}{displayText(download)}</div>{displayText(frameError && <p role="alert" className="archive-error">{displayText(frameError)}</p>)}
  </section>)}
 </>;
}
