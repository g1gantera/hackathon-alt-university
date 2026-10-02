import {useLanguage} from './language';
import {LanguageSwitcher} from './LanguageSwitcher';
import {translate, displayText} from './i18n/core.ts';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { App as AntApp, Button, Drawer, Form, Input, InputNumber, Modal, Select, Spin, Tag, Dropdown, Tooltip } from 'antd';
import { ChartLine, Check, ChevronRight, Clock3, Earth, History, Info, Map as MapIcon, Moon, MoreHorizontal, Pause, Play, RotateCcw, Search, ShieldCheck, Sun, SunMoon, TrainFront, TriangleAlert, Workflow, X } from 'lucide-react';
import { RailMap, TrackDiagram } from './Map';
import { TrainChart } from './Charts';
import { SpeedAdvicePanel } from './SpeedAdvicePanel';
import { api, clock, minutes, useDispatch } from './store';
import { connectRealtime, watchFreshness } from './realtime';
import { committedSnapshot, paintAttributes, paintIdentifier } from './performanceProbe';
import { TrainTelemetry } from './TrainTelemetry';
import { DispatcherPanel } from './DispatcherPanel';
import { ReplanningPanel } from './ReplanningPanel';
import { PlanComparisonPanel } from './PlanComparisonPanel';
import { QualityDetails } from './QualityDetails';
import { QualityPanel } from './QualityPanel';
import { qualityDriver, qualityStatus } from './quality';
import { SettingsDrawer } from './SettingsDrawer';
import { SpeedControl } from './SpeedControl';
import { HistoryPanel } from './HistoryPanel';
import { RouteProgress } from './RouteProgress';
import { setThemeMode, useTheme, type ThemeMode } from './theme';
import { useLens } from './liquid';
import type { Plan, Profile, Snapshot, Topology, Train } from './types';
const statusName: Record<string, string> = { get waiting() {
        return translate("На станции");
    }, get moving() {
        return translate("В движении");
    }, get completed() {
        return translate("Прибыл");
    } };
const incidentName: Record<string, string> = { get delay() {
        return translate("Задержка поезда");
    }, get closure() {
        return translate("Закрытие перегона");
    }, get signal() {
        return translate("Неисправность сигнала");
    } };
const replanName: Record<string, string> = { get idle() {
        return translate("Ожидает событий");
    }, get queued() {
        return translate("События объединяются");
    }, get calculating() {
        return translate("Идёт расчёт");
    }, get review() {
        return translate("Нужен выбор плана");
    }, get applied() {
        return translate("План применён");
    }, get failed() {
        return translate("Расчёт не завершён");
    } };
const roleName: Record<string, string> = { get viewer() {
        return translate("Наблюдатель");
    }, get dispatcher() {
        return translate("Диспетчер");
    }, get admin() {
        return translate("Администратор");
    } };
const themeName: Record<ThemeMode, string> = { get system() {
        return translate("Как в системе");
    }, get light() {
        return translate("Светлая");
    }, get dark() {
        return translate("Тёмная");
    } };
const speedPresets = [1, 10, 60];
const filters = [{ key: 'all', get label() {
            return translate("Все");
        } }, { key: 'moving', get label() {
            return translate("В пути");
        } }, { key: 'waiting', get label() {
            return translate("Стоят");
        } }, { key: 'late', get label() {
            return translate("Задержка");
        } }, { key: 'completed', get label() {
            return translate("Прибыли");
        } }] as const;
type Filter = typeof filters[number]['key'];
type Panel = 'map' | 'dispatch' | 'analytics';
const matches = (train: Train, filter: Filter) => filter === 'all' || (filter === 'late' ? train.delay_s > 0 : train.status === filter);
export default function App() {
    const language=useLanguage();
    const { message, modal } = AntApp.useApp();
    const { snapshot, selected, select, connected, lastUpdate, setSnapshot, setConnected } = useDispatch();
    const [topology, setTopology] = useState<Topology | null>(null), [baseline, setBaseline] = useState<Plan | null>(null), [plans, setPlans] = useState<Plan[]>([]), [preview, setPreview] = useState<Plan | null>(null);
    const [profile, setProfile] = useState<Profile | null>(null), [allCountry, setAllCountry] = useState(false), [mapTab, setMapTab] = useState('map');
    const [panel, setPanel] = useState<Panel>('map'), [detailOpen, setDetailOpen] = useState(false), [legendOpen, setLegendOpen] = useState(false), [qualityOpen, setQualityOpen] = useState(false);
    const [query, setQuery] = useState(''), [filter, setFilter] = useState<Filter>('all');
    const [incidentOpen, setIncidentOpen] = useState(false), [incidentKind, setIncidentKind] = useState('closure'), [incidentTarget, setIncidentTarget] = useState('section-1'), [duration, setDuration] = useState(10);
    const [settingsOpen, setSettingsOpen] = useState(false), [historyOpen, setHistoryOpen] = useState(false), [archive, setArchive] = useState<Snapshot | null>(null);
    const [auth, setAuth] = useState<{
        role: string;
        demo: boolean;
    } | null>(null), [loginNeeded, setLoginNeeded] = useState(false), [fatal, setFatal] = useState(''), [busy, setBusy] = useState(false), [stale, setStale] = useState(false);
    const { mode: themeMode, theme } = useTheme();
    const view = archive ?? snapshot;
    const ready = !!(view && topology);
    const [camera, setCamera] = useState<'2d' | '3d'>('2d'), [follow, setFollow] = useState(false);
    const [focusRequest, setFocusRequest] = useState(0);
    const navRef = useRef<HTMLElement>(null), segRef = useRef<HTMLDivElement>(null);
    useLens(navRef, `${historyOpen || archive !== null ? 'history' : panel}:${ready}:${language}`);
    useLens(segRef, `${camera}:${mapTab}:${ready}:${language}`);
    useEffect(() => { if (view && topology)
        committedSnapshot(view, 'dashboard'); }, [view, topology]);
    const historical = archive !== null;
    const showArchive = useCallback((value: Snapshot | null) => { setArchive(value); setPreview(null); }, []);
    const canControl = !!auth && auth.role !== 'viewer' && !historical && connected && !stale;
    const selectedTrain = view?.trains.find(t => t.id === selected);
    const loadPlans = useCallback(async () => { const data = await api<{
        plans: Plan[];
        baseline: Plan;
    }>('/plans'); setPlans(data.plans); setBaseline(data.baseline); }, []);
    const refresh = useCallback(async () => { setSnapshot(await api<Snapshot>('/state')); await loadPlans(); }, [setSnapshot, loadPlans]);
    useEffect(() => { api<{
        role: string;
        demo: boolean;
    }>('/auth/me').then(setAuth).catch(() => setLoginNeeded(true)); }, []);
    useEffect(() => {
        if (!auth)
            return;
        let stopped = false;
        const stop = connectRealtime({
            snapshot: s => setSnapshot(s, true), connected: setConnected,
            ready: () => { setPreview(null); Promise.all([api<Topology>('/topology'), loadPlans()]).then(([t]) => { if (!stopped) {
                setTopology(t);
                setFatal('');
            } }).catch(e => { if (!stopped)
                setFatal(e.message); }); },
            unauthorized: () => { setLoginNeeded(true); setAuth(null); },
            event: (type, payload) => {
                if (['replan.completed', 'plan.applied', 'simulation.changed', 'eco.plan_ready'].includes(type)) {
                    loadPlans().catch(() => { });
                    if (type === 'plan.applied')
                        setPreview(null);
                }
                if (['incident.created', 'incident.resolved', 'replan.queued', 'settings.updated'].includes(type) || (type === 'simulation.changed' && payload.action === 'reset')) {
                    setPreview(null);
                    setPlans([]);
                }
                if (type === 'replan.failed')
                    message.warning(displayText(payload.message) || translate("Допустимый план не найден"));
            },
        });
        return () => { stopped = true; stop(); };
    }, [auth, refresh, loadPlans, setSnapshot, setConnected, message]);
    useEffect(() => watchFreshness(lastUpdate, setStale), [lastUpdate]);
    useEffect(() => { let cancelled = false; setProfile(null); if (auth && !historical)
        api<Profile>(`/trains/${selected}/profile`).then(p => { if (!cancelled)
            setProfile(p); }).catch(() => { }); return () => { cancelled = true; }; }, [selected, snapshot?.active_plan_id, snapshot?.epoch, snapshot?.constraint_version, snapshot?.awaiting_plan, snapshot?.dispatch?.valid, auth, historical, connected]);
    useEffect(() => {
        // Escape closes the topmost floating panel; drawers and dialogs handle their own.
        const close = (event: KeyboardEvent) => {
            if (event.key !== 'Escape' || qualityOpen || settingsOpen || historyOpen || incidentOpen)
                return;
            if (legendOpen)
                setLegendOpen(false);
            else if (panel !== 'map')
                setPanel('map');
            else
                setDetailOpen(false);
        };
        addEventListener('keydown', close);
        return () => removeEventListener('keydown', close);
    }, [panel, legendOpen, qualityOpen, settingsOpen, historyOpen, incidentOpen]);
    const action = async (path: string, body?: unknown) => { setBusy(true); try {
        await api(path, 'POST', body);
        await refresh();
    }
    catch (e) {
        message.error((e as Error).message);
    }
    finally {
        setBusy(false);
    } };
    const calculateDispatch = async () => { setBusy(true); try {
        await api('/simulation/pause', 'POST');
        await api('/replan', 'POST');
        await refresh();
    }
    catch (e) {
        message.error((e as Error).message);
    }
    finally {
        setBusy(false);
    } };
    const openSettings = () => setSettingsOpen(true);
    const createIncident = async () => { setBusy(true); try {
        await api('/incidents', 'POST', { kind: incidentKind, target_id: incidentTarget, duration_s: duration * 60 });
        setIncidentOpen(false);
        setPlans([]);
        await refresh();
        setPanel('dispatch');
        message.success(translate("Сбой зарегистрирован. Новые отправления удерживаются до применения плана."));
    }
    catch (e) {
        message.error((e as Error).message);
    }
    finally {
        setBusy(false);
    } };
    const changeIncidentKind = (kind: string) => { setIncidentKind(kind); setIncidentTarget(kind === 'delay' ? view?.trains.find(t => t.status === 'waiting')?.id || '' : 'section-1'); };
    const chooseTrain = (id: string) => { select(id); setDetailOpen(true); setMapTab('map'); setAllCountry(false); setFollow(true); setFocusRequest(n => n + 1); };
    if (loginNeeded)
        return <div className="rf-splash"><div className="rf-language-login"><LanguageSwitcher/></div><div className="glass rf-splash-card"><span className="rf-logo lg"><TrainFront size={26}/></span><h1>RailFlow</h1><p>{translate("Вход в диспетчерский центр")}</p><Form layout="vertical" initialValues={{ role: 'viewer' }} onFinish={async (values) => { try {
            await api('/auth/login', 'POST', values);
            setAuth(await api('/auth/me'));
            setLoginNeeded(false);
        }
        catch (e) {
            message.error((e as Error).message);
        } }}><Form.Item name="role" label={translate("Роль")}><Select options={Object.entries(roleName).map(([value, label]) => ({ value, label }))}/></Form.Item><Form.Item name="password" label={translate("Пароль")} rules={[{ required: true }]}><Input.Password /></Form.Item><Button htmlType="submit" type="primary" block>{translate("Войти")}</Button></Form></div></div>;
    if (!view || !topology)
        return <div className="rf-splash"><div className="rf-language-login"><LanguageSwitcher/></div><div className="glass rf-splash-card"><span className="rf-logo lg"><TrainFront size={26}/></span><h1>RailFlow</h1>{displayText(!fatal && <Spin />)}<p>{displayText(fatal || translate("Подготавливаем участок Астана — Кокшетау…"))}</p>{displayText(fatal && <Button type="primary" onClick={() => location.reload()}>{translate("Повторить подключение")}</Button>)}</div></div>;
    const moving = view.trains.filter(t => t.status === 'moving').length;
    const blocked = view.metrics.blocked_sections?.length ?? view.sections.filter(s => ['closed', 'signal_failure'].includes(s.status)).length;
    const holdNotice = view.replanning ? translate("Отправления удерживаются до завершения расчёта.") : view.replan_status?.status === 'failed' ? translate("Расчёт не завершён. Устраните сбой или повторите попытку.") : translate("Отправления удерживаются. Примените новый план.");
    const quality = qualityStatus(view.metrics);
    const thresholds = view.metrics.formula?.thresholds ?? { normal: 90, attention: 70 };
    const replan = view.replan_status;
    const review = replan?.status === 'review';
    const activeIncidents = view.incidents.filter(i => i.start_s <= view.sim_time_s && i.end_s > view.sim_time_s && i.resolved_s === undefined);
    const troubled = new Set(activeIncidents.filter(i => i.kind === 'delay').map(i => i.target_id));
    const station = (id: string) => topology.stations.find(s => s.id === id)?.name || id;
    const shownTrains = view.trains.filter(t => matches(t, filter) && t.number.includes(query.trim().replace(/^№\s*/, '')));
    const notice = historical ? { tone: 'archive', text: translate("Архив: показан сохранённый снимок. Управление отключено.") } : !connected || stale ? { tone: 'stale', text: translate("Нет свежих данных. Переподключаемся…") } : view.awaiting_plan ? { tone: 'hold', text: holdNotice } : null;
    const sections: {
        key: Panel;
        label: string;
        icon: ReactNode;
    }[] = [{ key: 'map', label: translate("Карта"), icon: <MapIcon size={15}/> }, { key: 'dispatch', label: translate("Диспетчер"), icon: <Workflow size={15}/> }, { key: 'analytics', label: translate("Аналитика"), icon: <ChartLine size={15}/> }];
    const sheet = panel === 'dispatch' ? { title: translate("Диспетчер"), subtitle: translate("Сбои, конфликты, приоритеты и варианты расписания") } : { title: translate("Аналитика"), subtitle: translate("График движения, скорость и энергия, качество движения") };
    return <div className={'rf-app' + (notice ? ' has-notice' : '')}>
  <div className="rf-stage" onClickCapture={e => { if ((e.target as HTMLElement).closest('.train-pin,.schematic g[role=button]'))
        setDetailOpen(true); }}>
   <RailMap topology={topology} snapshot={view} allCountry={allCountry} mode={camera} active={mapTab === 'map'} follow={follow} onFollowChange={setFollow} historical={historical} focusRequest={focusRequest}/>
   {displayText(mapTab === 'scheme' && <div className="rf-scheme"><TrackDiagram topology={topology} snapshot={view}/></div>)}
  </div>

  <header className="glass clear rf-header">
   <div className="rf-brand"><span className="rf-logo"><TrainFront size={18}/></span><span className="rf-wordmark">RailFlow</span><span className="rf-sim" title={translate("Консультативная система поддержки решений на моделируемых данных")}>{displayText(auth?.demo ? translate("СИМУЛЯЦИЯ") : roleName[auth?.role ?? ''] || auth?.role)}</span></div>
   <div className="rf-route"><strong>{translate("Астана — Кокшетау")}</strong><span>{displayText(topology.stations.length)}{translate(" станций · ")}{displayText((topology.length_m / 1000).toFixed(1))}{translate(" км")}</span></div>
   <nav ref={navRef} className="rf-nav rf-lens" aria-label={translate("Разделы")}>
    {displayText(sections.map(s => <button key={s.key} className={panel === s.key && !historyOpen && !historical ? 'on' : ''} aria-current={panel === s.key ? 'page' : undefined} onClick={() => setPanel(s.key)}>{displayText(s.icon)}<span>{displayText(s.label)}</span>{displayText(s.key === 'dispatch' && review && <i className="rf-badge" title={translate("Нужен выбор плана")}/>)}</button>))}
    <button className={historyOpen || historical ? 'on' : ''} onClick={() => setHistoryOpen(true)}><History size={15}/><span>{translate("История")}</span></button>
   </nav>
   <span className="rf-spacer"/><LanguageSwitcher/>
   <div className="rf-clock" title={translate("Время модели")}><Clock3 size={16}/>{displayText(clock(view.sim_time_s))}<small>{translate("модель · ")}{displayText(view.speed)}×</small></div>
   <div className={'rf-live' + (connected && !stale ? '' : ' bad') + (historical ? ' archive' : '')}><i />{displayText(historical ? translate("Архив") : connected && !stale ? translate("В эфире") : translate("Нет данных"))}</div>
   <button className="rf-icon-btn rf-theme" aria-label={displayText(theme === 'dark' ? translate("Включить светлую тему") : translate("Включить тёмную тему"))} title={displayText(translate("Тема: {0}", themeName[themeMode].toLowerCase()))} onClick={e => setThemeMode(theme === 'dark' ? 'light' : 'dark', { x: e.clientX, y: e.clientY })}>{displayText(theme === 'dark' ? <Sun size={18}/> : <Moon size={18}/>)}</button>
   <Dropdown trigger={['click']} menu={{ selectable: true, selectedKeys: ['theme:' + themeMode], items: [{ key: 'settings', label: translate("Настройки сценария") }, { key: 'demo', label: <a href="/stage3.html">{translate("Отдельное демо автодиспетчера ↗")}</a> }, { type: 'divider' }, { type: 'group', label: translate("Тема"), children: ([['system', <SunMoon size={15}/>], ['light', <Sun size={15}/>], ['dark', <Moon size={15}/>]] as const).map(([key, icon]) => ({ key: 'theme:' + key, icon, label: themeName[key] })) }, ...(!auth?.demo ? [{ type: 'divider' as const }, { key: 'logout', label: translate("Выйти") }] : [])], onClick: ({ key, domEvent }) => { if (key.startsWith('theme:'))
            setThemeMode(key.slice(6) as ThemeMode, 'clientX' in domEvent ? { x: domEvent.clientX, y: domEvent.clientY } : undefined); if (key === 'settings')
            void openSettings(); if (key === 'logout')
            void api('/auth/logout', 'POST').then(() => location.reload()); } }}><button className="rf-icon-btn" aria-label={translate("Дополнительные инструменты")}><MoreHorizontal size={18}/></button></Dropdown>
  </header>

  {displayText(notice && <div className={'glass rf-notice ' + notice.tone} role="status"><TriangleAlert size={16}/><span>{displayText(notice.text)}</span>{displayText(historical ? <Button size="small" onClick={() => setHistoryOpen(true)}>{translate("История и отчёт")}</Button> : review && <Button size="small" type="primary" onClick={() => setPanel('dispatch')}>{translate("Открыть варианты")}</Button>)}</div>)}

  <div className="rf-left">
   <section className={'glass rf-quality-card tone-' + quality.tone}>
    <button className="rf-quality" onClick={() => setQualityOpen(true)} aria-label={displayText(translate("Индекс качества движения ") + view.metrics.index.toFixed(1) + translate(" из 100. ") + quality.label + translate(". Подробнее"))}>
     <span className="rf-quality-head"><span className="rf-kicker">{translate("Индекс качества")}</span><span className="rf-pill">{displayText(quality.label)}</span></span>
     <span className="rf-quality-score"><strong key={paintIdentifier(view, 'dashboard') ?? 'quality'} {...paintAttributes(view, 'dashboard')}>{displayText(view.metrics.index.toFixed(1))}</strong><small>/ 100</small></span>
     <span className="rf-scale" aria-hidden="true"><i style={{ width: Math.min(100, Math.max(0, view.metrics.index)) + '%' }}/><em style={{ left: thresholds.attention + '%' }}/><em style={{ left: thresholds.normal + '%' }}/></span>
     <span className="rf-quality-driver"><span>{displayText(qualityDriver(view.metrics))}</span><ChevronRight size={15}/></span>
    </button>
    <div className="rf-kpis">
     <div><span>{translate("В движении")}</span><strong>{displayText(moving)}<small>{translate(" из ")}{displayText(view.trains.length)}</small></strong></div>
     <div><span>{translate("Прибыли")}</span><strong>{displayText(view.metrics.completed_trips)}<small>{translate(" поездов")}</small></strong></div>
     <div title={translate("По прибытиям на станции, включая ожидание")}><span>{translate("Задержка")}</span><strong>{displayText((view.metrics.total_delay_s / 60).toFixed(1))}<small>{translate(" мин")}</small></strong></div>
     <div className={blocked ? 'alert' : ''}><span>{translate("Открытые перегоны")}</span><strong>{displayText(view.sections.length - blocked)}<small>{translate(" из ")}{displayText(view.sections.length)}</small></strong></div>
    </div>
   </section>
   {displayText(replan && <button className={'glass rf-ops state-' + replan.status} onClick={() => setPanel('dispatch')}>
    <span className="rf-ops-icon">{displayText(view.replanning ? <Spin size="small"/> : replan.status === 'applied' ? <Check size={17}/> : replan.status === 'idle' ? <RotateCcw size={16}/> : <TriangleAlert size={16}/>)}</span>
    <span className="rf-ops-text"><strong>{displayText(replanName[replan.status])}</strong><small>{translate("Сбоев: ")}{displayText(activeIncidents.length)}{translate(" · нарушений: ")}{displayText(view.metrics.conflicts)}{displayText(replan.elapsed_s !== undefined && replan.status !== 'idle' ? translate(" · {0} с", replan.elapsed_s.toFixed(2)) : '')}</small></span>
    <ChevronRight size={15}/>
   </button>)}
   <p className="rf-advisory"><ShieldCheck size={14}/>{translate("Консультативный прототип: решения принимает диспетчер. Не заменяет СЦБ и системы безопасности движения.")}</p>
  </div>

  <aside className="glass rf-fleet" aria-label={translate("Поезда")}>
   <div className="rf-fleet-head"><h2>{translate("Поезда ")}<span>{displayText(view.trains.length)}</span></h2></div>
   <label className="rf-search"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder={translate("Номер поезда")} aria-label={translate("Поиск поезда по номеру")} inputMode="numeric"/>{displayText(query && <button aria-label={translate("Очистить поиск")} onClick={() => setQuery('')}><X size={13}/></button>)}</label>
   <div className="rf-chips" role="group" aria-label={translate("Фильтр поездов")}>{displayText(filters.map(f => <button key={f.key} className={filter === f.key ? 'on' : ''} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>{displayText(f.label)}<span>{displayText(view.trains.filter(t => matches(t, f.key)).length)}</span></button>))}</div>
   <div className="rf-rows">{displayText(shownTrains.length ? shownTrains.map(train => {
            const late = train.delay_s > 0;
            return <button key={train.id} className={`rf-train ${train.status}${selected === train.id ? ' sel' : ''}`} aria-pressed={selected === train.id} onClick={() => chooseTrain(train.id)}>
     <span className={'rf-train-icon ' + train.type}><span style={{ transform: `rotate(${train.bearing_deg}deg)` }}>↑</span></span>
     {displayText(troubled.has(train.id) && <TriangleAlert className="rf-train-alert" size={13} aria-label={translate("Активный сбой")}/>)}
     <span className="rf-train-main"><strong>№ {displayText(train.number)}</strong><small title={displayText(train.route_name)}>{displayText(train.type === 'passenger' ? translate("Пасс.") : translate("Груз."))} · → {displayText(station(train.destination_id))}</small></span>
     <span className="rf-train-side"><strong>{displayText((train.speed_mps * 3.6).toFixed(0))}<small>{translate(" км/ч")}</small></strong><span className={'rf-state ' + (late ? 'late' : train.status)}>{displayText(late ? `+${minutes(train.delay_s)}` : statusName[train.status])}</span></span>
    </button>;
        }) : <p className="rf-empty">{translate("Нет поездов по этому фильтру.")}</p>)}</div>
  </aside>

  {displayText(detailOpen && selectedTrain && panel === 'map' && <section className="glass rf-detail" aria-label={displayText(translate("Поезд № ") + selectedTrain.number)}>
   <div className="rf-detail-head"><span className={'rf-train-icon lg ' + selectedTrain.type}><TrainFront size={18}/></span><div><h3>{translate("Поезд № ")}{displayText(selectedTrain.number)}</h3><p>{displayText(selectedTrain.type === 'passenger' ? translate("Пассажирский") : translate("Грузовой"))} · {displayText(statusName[selectedTrain.status] || selectedTrain.status)}</p></div><button className="rf-close" aria-label={translate("Закрыть карточку поезда")} onClick={() => setDetailOpen(false)}><X size={16}/></button></div>
   <RouteProgress train={selectedTrain} topology={topology}/>
   <TrainTelemetry train={selectedTrain} snapshot={view} historical={historical} topology={topology}/>
   <div className="rf-detail-actions"><Button icon={<ChartLine size={15}/>} onClick={() => setPanel('analytics')}>{translate("Скорость и энергия")}</Button><Button icon={<Workflow size={15}/>} onClick={() => setPanel('dispatch')}>{translate("Решения диспетчера")}</Button></div>
  </section>)}

  {displayText(panel !== 'map' && <section className="glass rf-sheet" aria-label={displayText(sheet.title)}>
   <div className="rf-sheet-head"><div><h2>{displayText(sheet.title)}{displayText(panel === 'dispatch' && review && <Tag color="orange">{translate("Нужен выбор")}</Tag>)}</h2><p>{displayText(sheet.subtitle)}</p></div><button className="rf-close" aria-label={translate("Закрыть панель")} onClick={() => setPanel('map')}><X size={16}/></button></div>
   <div className="rf-sheet-body">{displayText(panel === 'dispatch' ? <>
    <ReplanningPanel snapshot={view} topology={topology} canControl={canControl && !busy} historical={historical} refresh={refresh}/>
    <DispatcherPanel snapshot={view} topology={topology} preview={historical ? null : preview} historical={historical} canControl={canControl && !busy} onCalculate={calculateDispatch}/>
    <section className="panel recommendation-panel"><div className="panel-heading"><h2>{translate("Варианты расписания")}</h2><span className="subtle">{translate("Прогноз")}</span></div>{displayText(plans.length && !historical ? <div className="plan-grid">{displayText(plans.map(plan => <article key={plan.id} className={'plan-card ' + (preview?.id === plan.id ? 'chosen' : '')}><div className="plan-top"><span className="plan-check"><Check size={17}/></span><h3>{displayText(plan.label)}</h3></div><div className="plan-stats"><div><span>{translate("Задержка")}</span><strong>{displayText(minutes(plan.metrics.total_delay_s))}</strong></div><div><span>{translate("Максимальная")}</span><strong>{displayText(minutes(plan.metrics.max_delay_s))}</strong></div><div><span>{translate("Энергия")}</span><strong>{displayText((plan.metrics.energy_kwh / 1000).toFixed(2))}{translate(" МВт·ч")}</strong></div><div><span>{translate("Индекс прогноза")}</span><strong>{displayText(plan.metrics.index.toFixed(1))}</strong></div></div><div className="plan-actions"><Button onClick={() => setPreview(plan)}>{translate("Сравнить график")}</Button><Button type="primary" disabled={!canControl || busy || view.replanning} onClick={() => action('/plans/' + plan.id + '/apply')}>{translate("Применить")}</Button></div></article>))}</div> : <div className="empty small">{displayText(historical ? translate("Вернитесь в эфир для расчёта.") : view.replanning ? translate("Рассчитываем варианты…") : translate("Нажмите «Пауза и расчёт», чтобы сравнить варианты расписания."))}</div>)}</section>
    {displayText(preview && !historical && <section className="panel diagram-panel"><div className="panel-heading"><h2>{displayText(preview.label)}</h2><Button onClick={() => setPreview(null)}>{translate("Закрыть сравнение")}</Button></div><PlanComparisonPanel planId={preview.id} snapshot={view} topology={topology} connected={connected && !stale}/><div className="comparison-chart-legend">{translate("┄ Действующий план · ━ Выбранный вариант")}</div><TrainChart topology={topology} snapshot={view} baseline={view.plan} preview={preview}/></section>)}
   </> : <div className="analytics-grid"><section className="panel diagram-panel"><div className="panel-heading"><h2>{translate("График движения")}</h2><div className="chart-legend"><span>{translate("┄ Исходный")}</span><span>{translate("━ Активный")}</span></div></div><TrainChart topology={topology} snapshot={view} baseline={baseline} preview={null}/></section><section className="panel speed-panel"><div className="panel-heading"><h2>{translate("Скорость и энергия")}</h2><Select aria-label={translate("Поезд для рекомендаций")} value={selected} onChange={select} options={view.trains.map(t => ({ value: t.id, label: '№ ' + t.number }))}/></div><SpeedAdvicePanel train={selectedTrain} snapshot={view} topology={topology} profile={profile} historical={historical} canControl={canControl && !busy} refresh={refresh}/></section><QualityPanel snapshot={view} topology={topology} historical={historical} active={panel === 'analytics'} connected={connected && !stale}/></div>)}</div>
  </section>)}

  {displayText(!historical && <div className="glass clear rf-controls" role="toolbar" aria-label={translate("Управление симуляцией")}>
   <Button type="primary" className="rf-run" disabled={!canControl} loading={busy} icon={view.running ? <Pause size={15}/> : <Play size={15}/>} onClick={() => action('/simulation/' + (view.running ? 'pause' : 'start'))}>{displayText(view.running ? translate("Пауза") : translate("Запустить"))}</Button>
   <Tooltip title={translate("Сбросить сценарий")}><Button shape="circle" aria-label={translate("Сброс")} disabled={!canControl || busy} icon={<RotateCcw size={15}/>} onClick={() => modal.confirm({ title: translate("Сбросить сценарий?"), content: translate("Движение вернётся к исходному состоянию."), okText: translate("Сбросить"), cancelText: translate("Отмена"), onOk: () => action('/simulation/reset').then(() => { setPreview(null); setArchive(null); }) })}/></Tooltip>
   <span className="rf-sep"/>
   <div className="rf-speed" role="group" aria-label={translate("Скорость времени модели")}><span>{translate("Время")}</span>{displayText(speedPresets.map(multiplier => <button key={multiplier} className={view.speed === multiplier ? 'on' : ''} aria-pressed={view.speed === multiplier} disabled={!canControl || busy} onClick={() => action('/simulation/speed', { multiplier })}>{displayText(multiplier)}×</button>))}<SpeedControl value={view.speed} disabled={!canControl || busy} onApply={multiplier => action('/simulation/speed', { multiplier })}/></div>
   <span className="rf-sep"/>
   <Button className="rf-incident" disabled={!canControl || busy} icon={<TriangleAlert size={15}/>} onClick={() => setIncidentOpen(true)}>{translate("Добавить сбой")}</Button>
  </div>)}

  <div className="glass clear rf-mapdock" role="toolbar" aria-label={translate("Вид карты")}>
   <div ref={segRef} className="rf-seg rf-lens">{displayText((['2d', '3d'] as const).map(mode => <button key={mode} className={mapTab === 'map' && camera === mode ? 'on' : ''} aria-pressed={mapTab === 'map' && camera === mode} onClick={() => { setMapTab('map'); setCamera(mode); if (mode === '3d')
        setAllCountry(false); }}>{displayText(mode === '2d' ? translate("Карта 2D") : translate("Карта 3D"))}</button>))}<button className={mapTab === 'scheme' ? 'on' : ''} aria-pressed={mapTab === 'scheme'} onClick={() => setMapTab('scheme')}>{translate("Схема путей")}</button></div>
   {displayText(mapTab === 'map' && <><button className={'rf-dock-btn' + (follow ? ' on' : '')} aria-pressed={follow} disabled={!selectedTrain} title={translate("Следовать за выбранным поездом. Перетаскивание карты отключает слежение.")} onClick={() => { if (!follow)
        setAllCountry(false); setFollow(x => !x); }}><TrainFront size={15}/><span>{translate("За поездом")}</span></button><button className="rf-dock-btn" onClick={() => { setFollow(false); setAllCountry(x => !x); }} aria-label={displayText(allCountry ? translate("К маршруту") : translate("Весь Казахстан"))}><Earth size={15}/><span>{displayText(allCountry ? translate("К маршруту") : translate("Весь Казахстан"))}</span></button></>)}
   <button className={'rf-dock-btn icon' + (legendOpen ? ' on' : '')} aria-label={translate("Легенда карты")} aria-expanded={legendOpen} onClick={() => setLegendOpen(x => !x)}><Info size={16}/></button>
  </div>
  {displayText(legendOpen && <div className="glass rf-legend" role="dialog" aria-label={translate("Легенда карты")}>
   <div className="rf-legend-head"><h4>{translate("Легенда")}</h4><button className="rf-close sm" aria-label={translate("Закрыть легенду")} onClick={() => setLegendOpen(false)}><X size={14}/></button></div>
   <ul>
    <li><i className="line corridor"/>{translate("Моделируемый участок")}</li>
    <li><i className="line occupied"/>{translate("Перегон занят поездом")}</li>
    <li><i className="line signal"/>{translate("Неисправность сигнала")}</li>
    <li><i className="line closed"/>{translate("Перегон закрыт")}</li>
    <li><i className="line network"/>{translate("Сеть Казахстана, OSM")}</li>
    <li><i className="pin passenger"/>{translate("Пассажирский поезд")}</li>
    <li><i className="pin freight"/>{translate("Грузовой поезд")}</li>
    <li><i className="pin stopped"/>{translate("Пунктир — поезд стоит")}</li>
   </ul>
   <p>{translate("Стрелка показывает направление движения. Геометрия — OpenStreetMap (ODbL), станции и сигналы — демомодель.")}</p>
  </div>)}

  <Drawer title={translate("Индекс качества движения")} open={qualityOpen} onClose={() => setQualityOpen(false)} width={520}><QualityDetails metrics={view.metrics} topology={topology}/></Drawer>
  <Modal title={translate("Добавить сбой")} open={incidentOpen} onCancel={() => setIncidentOpen(false)} onOk={createIncident} confirmLoading={busy} okText={translate("Добавить и пересчитать")} cancelText={translate("Отмена")} okButtonProps={{ disabled: !incidentTarget || !canControl }}><div className="modal-fields"><label>{translate("Тип события")}<Select value={incidentKind} onChange={changeIncidentKind} options={Object.entries(incidentName).map(([value, label]) => ({ value, label }))}/></label><label>{displayText(incidentKind === 'delay' ? translate("Поезд на станции") : translate("Перегон"))}<Select value={incidentTarget || undefined} onChange={setIncidentTarget} options={incidentKind === 'delay' ? view.trains.filter(t => t.status === 'waiting').map(t => ({ value: t.id, label: `№ ${t.number}` })) : topology.sections.map((s, i) => ({ value: s.id, label: `${topology.stations[i].name} — ${topology.stations[i + 1].name}` }))}/></label><label>{translate("Длительность, минут")}<InputNumber value={duration} min={1} max={120} onChange={n => setDuration(n || 10)}/></label><p className="subtle">{translate("Поезда, уже вошедшие на перегон, освобождают его. Новые отправления удерживаются до применения проверенного плана.")}</p></div></Modal>
  {displayText(snapshot && <HistoryPanel live={snapshot} topology={topology} open={historyOpen} onClose={() => setHistoryOpen(false)} onView={showArchive}/>)}
  <SettingsDrawer open={settingsOpen} epoch={snapshot?.epoch || view.epoch} canEdit={auth?.role === 'admin' && canControl} onClose={() => setSettingsOpen(false)} onSaved={refresh}/>
 </div>;
}
