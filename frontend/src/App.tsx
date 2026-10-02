import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {App as AntApp,Button,Drawer,Form,Input,InputNumber,Modal,Select,Spin,Tag,Dropdown,Tooltip} from 'antd';
import {ChartLine,Check,ChevronRight,Clock3,Earth,History,Info,Map as MapIcon,Moon,MoreHorizontal,Pause,Play,RotateCcw,Search,ShieldCheck,Sun,SunMoon,TrainFront,TriangleAlert,Workflow,X} from 'lucide-react';
import {RailMap,TrackDiagram} from './Map';
import {TrainChart} from './Charts';
import {SpeedAdvicePanel} from './SpeedAdvicePanel';
import {api,clock,minutes,useDispatch} from './store';
import {connectRealtime,watchFreshness} from './realtime';
import {committedSnapshot,paintAttributes,paintIdentifier} from './performanceProbe';
import {TrainTelemetry} from './TrainTelemetry';
import {DispatcherPanel} from './DispatcherPanel';
import {ReplanningPanel} from './ReplanningPanel';
import {PlanComparisonPanel} from './PlanComparisonPanel';
import {QualityDetails} from './QualityDetails';
import {QualityPanel} from './QualityPanel';
import {qualityDriver,qualityStatus} from './quality';
import {SettingsDrawer} from './SettingsDrawer';
import {SpeedControl} from './SpeedControl';
import {HistoryPanel} from './HistoryPanel';
import {RouteProgress} from './RouteProgress';
import {setThemeMode,useTheme,type ThemeMode} from './theme';
import {useLens} from './liquid';
import {Rail3DView} from './Rail3DView';
import type {CameraMode} from './rail3d/scene';
import type {Plan,Profile,Snapshot,Topology,Train} from './types';

const statusName:Record<string,string>={waiting:'На станции',moving:'В движении',completed:'Прибыл'};
const incidentName:Record<string,string>={delay:'Задержка поезда',closure:'Закрытие перегона',signal:'Неисправность сигнала'};
const replanName:Record<string,string>={idle:'Ожидает событий',queued:'События объединяются',calculating:'Идёт расчёт',review:'Нужен выбор плана',applied:'План применён',failed:'Расчёт не завершён'};
const roleName:Record<string,string>={viewer:'Наблюдатель',dispatcher:'Диспетчер',admin:'Администратор'};
const themeName:Record<ThemeMode,string>={system:'Как в системе',light:'Светлая',dark:'Тёмная'};
const speedPresets=[1,10,60];
const filters=[{key:'all',label:'Все'},{key:'moving',label:'В пути'},{key:'waiting',label:'Стоят'},{key:'late',label:'Задержка'},{key:'completed',label:'Прибыли'}] as const;
type Filter=typeof filters[number]['key'];
type Panel='map'|'dispatch'|'analytics';
const matches=(train:Train,filter:Filter)=>filter==='all'||(filter==='late'?train.delay_s>0:train.status===filter);

export default function App(){
 const {message,modal}=AntApp.useApp();
 const {snapshot,selected,select,connected,lastUpdate,setSnapshot,setConnected}=useDispatch();
 const [topology,setTopology]=useState<Topology|null>(null),[baseline,setBaseline]=useState<Plan|null>(null),[plans,setPlans]=useState<Plan[]>([]),[preview,setPreview]=useState<Plan|null>(null);
 const [profile,setProfile]=useState<Profile|null>(null),[allCountry,setAllCountry]=useState(false),[mapTab,setMapTab]=useState('map');
 const [panel,setPanel]=useState<Panel>('map'),[detailOpen,setDetailOpen]=useState(false),[legendOpen,setLegendOpen]=useState(false),[qualityOpen,setQualityOpen]=useState(false);
 const [query,setQuery]=useState(''),[filter,setFilter]=useState<Filter>('all');
 const [incidentOpen,setIncidentOpen]=useState(false),[incidentKind,setIncidentKind]=useState('closure'),[incidentTarget,setIncidentTarget]=useState('section-1'),[duration,setDuration]=useState(10);
 const [settingsOpen,setSettingsOpen]=useState(false),[historyOpen,setHistoryOpen]=useState(false),[archive,setArchive]=useState<Snapshot|null>(null);
 const [auth,setAuth]=useState<{role:string;demo:boolean}|null>(null),[loginNeeded,setLoginNeeded]=useState(false),[fatal,setFatal]=useState(''),[busy,setBusy]=useState(false),[stale,setStale]=useState(false);
 const {mode:themeMode,theme}=useTheme();
 const view=archive??snapshot;
 const ready=!!(view&&topology);
 const [camera,setCamera]=useState<CameraMode>('3d');
 const navRef=useRef<HTMLElement>(null),segRef=useRef<HTMLDivElement>(null),cameraRef=useRef<HTMLDivElement>(null);
 useLens(navRef,`${historyOpen||archive!==null?'history':panel}:${ready}`);
 useLens(segRef,`${mapTab}:${ready}`);
 useLens(cameraRef,`${camera}:${mapTab}:${ready}`);
 useEffect(()=>{if(view&&topology)committedSnapshot(view,'dashboard');},[view,topology]);
 const historical=archive!==null;
 const showArchive=useCallback((value:Snapshot|null)=>{setArchive(value);setPreview(null);},[]);
 const canControl=!!auth&&auth.role!=='viewer'&&!historical&&connected&&!stale;
 const selectedTrain=view?.trains.find(t=>t.id===selected);
 const loadPlans=useCallback(async()=>{const data=await api<{plans:Plan[];baseline:Plan}>('/plans');setPlans(data.plans);setBaseline(data.baseline);},[]);
 const refresh=useCallback(async()=>{setSnapshot(await api<Snapshot>('/state'));await loadPlans();},[setSnapshot,loadPlans]);
 useEffect(()=>{api<{role:string;demo:boolean}>('/auth/me').then(setAuth).catch(()=>setLoginNeeded(true));},[]);
 useEffect(()=>{
  if(!auth)return;
  let stopped=false;
  const stop=connectRealtime({
   snapshot:s=>setSnapshot(s,true),connected:setConnected,
   ready:()=>{setPreview(null);Promise.all([api<Topology>('/topology'),loadPlans()]).then(([t])=>{if(!stopped){setTopology(t);setFatal('');}}).catch(e=>{if(!stopped)setFatal(e.message);});},
   unauthorized:()=>{setLoginNeeded(true);setAuth(null);},
   event:(type,payload)=>{
    if(['replan.completed','plan.applied','simulation.changed','eco.plan_ready'].includes(type)){loadPlans().catch(()=>{});if(type==='plan.applied')setPreview(null);}
    if(['incident.created','incident.resolved','replan.queued','settings.updated'].includes(type)||(type==='simulation.changed'&&payload.action==='reset')){setPreview(null);setPlans([]);}
    if(type==='replan.failed')message.warning(payload.message||'Допустимый план не найден');
   },
  });
  return()=>{stopped=true;stop();};
 },[auth,refresh,loadPlans,setSnapshot,setConnected,message]);
 useEffect(()=>watchFreshness(lastUpdate,setStale),[lastUpdate]);
 useEffect(()=>{let cancelled=false;setProfile(null);if(auth&&!historical)api<Profile>(`/trains/${selected}/profile`).then(p=>{if(!cancelled)setProfile(p);}).catch(()=>{});return()=>{cancelled=true;};},[selected,snapshot?.active_plan_id,snapshot?.epoch,snapshot?.constraint_version,snapshot?.awaiting_plan,snapshot?.dispatch?.valid,auth,historical,connected]);
 useEffect(()=>{
  // Escape closes the topmost floating panel; drawers and dialogs handle their own.
  const close=(event:KeyboardEvent)=>{
   if(event.key!=='Escape'||qualityOpen||settingsOpen||historyOpen||incidentOpen)return;
   if(legendOpen)setLegendOpen(false);else if(panel!=='map')setPanel('map');else setDetailOpen(false);
  };
  addEventListener('keydown',close);
  return()=>removeEventListener('keydown',close);
 },[panel,legendOpen,qualityOpen,settingsOpen,historyOpen,incidentOpen]);
 const action=async(path:string,body?:unknown)=>{setBusy(true);try{await api(path,'POST',body);await refresh();}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 const calculateDispatch=async()=>{setBusy(true);try{await api('/simulation/pause','POST');await api('/replan','POST');await refresh();}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};

 const openSettings=()=>setSettingsOpen(true);
 const createIncident=async()=>{setBusy(true);try{await api('/incidents','POST',{kind:incidentKind,target_id:incidentTarget,duration_s:duration*60});setIncidentOpen(false);setPlans([]);await refresh();setPanel('dispatch');message.success('Сбой зарегистрирован. Новые отправления удерживаются до применения плана.');}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 const changeIncidentKind=(kind:string)=>{setIncidentKind(kind);setIncidentTarget(kind==='delay'?view?.trains.find(t=>t.status==='waiting')?.id||'':'section-1');};
 const chooseTrain=(id:string)=>{select(id);setDetailOpen(true);};

 if(loginNeeded)return <div className="rf-splash"><div className="glass rf-splash-card"><span className="rf-logo lg"><TrainFront size={26}/></span><h1>RailFlow</h1><p>Вход в диспетчерский центр</p><Form layout="vertical" initialValues={{role:'viewer'}} onFinish={async values=>{try{await api('/auth/login','POST',values);setAuth(await api('/auth/me'));setLoginNeeded(false);}catch(e){message.error((e as Error).message);}}}><Form.Item name="role" label="Роль"><Select options={Object.entries(roleName).map(([value,label])=>({value,label}))}/></Form.Item><Form.Item name="password" label="Пароль" rules={[{required:true}]}><Input.Password/></Form.Item><Button htmlType="submit" type="primary" block>Войти</Button></Form></div></div>;
 if(!view||!topology)return <div className="rf-splash"><div className="glass rf-splash-card"><span className="rf-logo lg"><TrainFront size={26}/></span><h1>RailFlow</h1>{!fatal&&<Spin/>}<p>{fatal||'Подготавливаем участок Астана — Кокшетау…'}</p>{fatal&&<Button type="primary" onClick={()=>location.reload()}>Повторить подключение</Button>}</div></div>;

 const moving=view.trains.filter(t=>t.status==='moving').length;
 const blocked=view.metrics.blocked_sections?.length??view.sections.filter(s=>['closed','signal_failure'].includes(s.status)).length;
 const holdNotice=view.replanning?'Отправления удерживаются до завершения расчёта.':view.replan_status?.status==='failed'?'Расчёт не завершён. Устраните сбой или повторите попытку.':'Отправления удерживаются. Примените новый план.';
 const quality=qualityStatus(view.metrics);
 const thresholds=view.metrics.formula?.thresholds??{normal:90,attention:70};
 const replan=view.replan_status;
 const review=replan?.status==='review';
 const activeIncidents=view.incidents.filter(i=>i.start_s<=view.sim_time_s&&i.end_s>view.sim_time_s&&i.resolved_s===undefined);
 const troubled=new Set(activeIncidents.filter(i=>i.kind==='delay').map(i=>i.target_id));
 const station=(id:string)=>topology.stations.find(s=>s.id===id)?.name||id;
 const shownTrains=view.trains.filter(t=>matches(t,filter)&&t.number.includes(query.trim().replace(/^№\s*/,'')));
 const notice=historical?{tone:'archive',text:'Архив: показан сохранённый снимок. Управление отключено.'}:!connected||stale?{tone:'stale',text:'Нет свежих данных. Переподключаемся…'}:view.awaiting_plan?{tone:'hold',text:holdNotice}:null;
 const sections:{key:Panel;label:string;icon:ReactNode}[]=[{key:'map',label:'Карта',icon:<MapIcon size={15}/>},{key:'dispatch',label:'Диспетчер',icon:<Workflow size={15}/>},{key:'analytics',label:'Аналитика',icon:<ChartLine size={15}/>}];
 const sheet=panel==='dispatch'?{title:'Диспетчер',subtitle:'Сбои, конфликты, приоритеты и варианты расписания'}:{title:'Аналитика',subtitle:'График движения, скорость и энергия, качество движения'};

 return <div className={'rf-app'+(notice?' has-notice':'')}>
  <div className={'rf-stage'+(mapTab==='3d'?' rf-stage-3d':'')} onClickCapture={e=>{if((e.target as HTMLElement).closest('.train-pin,.schematic g[role=button],.r3d-label'))setDetailOpen(true);}}>
   {mapTab==='map'?<RailMap topology={topology} snapshot={view} allCountry={allCountry}/>:mapTab==='scheme'?<div className="rf-scheme"><TrackDiagram topology={topology} snapshot={view}/></div>:<Rail3DView topology={topology} snapshot={view} camera={camera}/>}
  </div>

  <header className="glass clear rf-header">
   <div className="rf-brand"><span className="rf-logo"><TrainFront size={18}/></span><span className="rf-wordmark">RailFlow</span><span className="rf-sim" title="Консультативная система поддержки решений на моделируемых данных">{auth?.demo?'СИМУЛЯЦИЯ':roleName[auth?.role??'']||auth?.role}</span></div>
   <div className="rf-route"><strong>Астана — Кокшетау</strong><span>{topology.stations.length} станций · {(topology.length_m/1000).toFixed(1)} км</span></div>
   <nav ref={navRef} className="rf-nav rf-lens" aria-label="Разделы">
    {sections.map(s=><button key={s.key} className={panel===s.key&&!historyOpen&&!historical?'on':''} aria-current={panel===s.key?'page':undefined} onClick={()=>setPanel(s.key)}>{s.icon}<span>{s.label}</span>{s.key==='dispatch'&&review&&<i className="rf-badge" title="Нужен выбор плана"/>}</button>)}
    <button className={historyOpen||historical?'on':''} onClick={()=>setHistoryOpen(true)}><History size={15}/><span>История</span></button>
   </nav>
   <span className="rf-spacer"/>
   <div className="rf-clock" title="Время модели"><Clock3 size={16}/>{clock(view.sim_time_s)}<small>модель · {view.speed}×</small></div>
   <div className={'rf-live'+(connected&&!stale?'':' bad')+(historical?' archive':'')}><i/>{historical?'Архив':connected&&!stale?'В эфире':'Нет данных'}</div>
   <button className="rf-icon-btn rf-theme" aria-label={theme==='dark'?'Включить светлую тему':'Включить тёмную тему'} title={`Тема: ${themeName[themeMode].toLowerCase()}`} onClick={e=>setThemeMode(theme==='dark'?'light':'dark',{x:e.clientX,y:e.clientY})}>{theme==='dark'?<Sun size={18}/>:<Moon size={18}/>}</button>
   <Dropdown trigger={['click']} menu={{selectable:true,selectedKeys:['theme:'+themeMode],items:[{key:'settings',label:'Настройки сценария'},{key:'demo',label:<a href="/stage3.html">Отдельное демо автодиспетчера ↗</a>},{type:'divider'},{type:'group',label:'Тема',children:([['system',<SunMoon size={15}/>],['light',<Sun size={15}/>],['dark',<Moon size={15}/>]] as const).map(([key,icon])=>({key:'theme:'+key,icon,label:themeName[key]}))},...(!auth?.demo?[{type:'divider' as const},{key:'logout',label:'Выйти'}]:[])],onClick:({key,domEvent})=>{if(key.startsWith('theme:'))setThemeMode(key.slice(6) as ThemeMode,'clientX' in domEvent?{x:domEvent.clientX,y:domEvent.clientY}:undefined);if(key==='settings')void openSettings();if(key==='logout')void api('/auth/logout','POST').then(()=>location.reload());}}}><button className="rf-icon-btn" aria-label="Дополнительные инструменты"><MoreHorizontal size={18}/></button></Dropdown>
  </header>

  {notice&&<div className={'glass rf-notice '+notice.tone} role="status"><TriangleAlert size={16}/><span>{notice.text}</span>{historical?<Button size="small" onClick={()=>setHistoryOpen(true)}>История и отчёт</Button>:review&&<Button size="small" type="primary" onClick={()=>setPanel('dispatch')}>Открыть варианты</Button>}</div>}

  <div className="rf-left">
   <section className={'glass rf-quality-card tone-'+quality.tone}>
    <button className="rf-quality" onClick={()=>setQualityOpen(true)} aria-label={'Индекс качества движения '+view.metrics.index.toFixed(1)+' из 100. '+quality.label+'. Подробнее'}>
     <span className="rf-quality-head"><span className="rf-kicker">Индекс качества</span><span className="rf-pill">{quality.label}</span></span>
     <span className="rf-quality-score"><strong key={paintIdentifier(view,'dashboard')??'quality'} {...paintAttributes(view,'dashboard')}>{view.metrics.index.toFixed(1)}</strong><small>/ 100</small></span>
     <span className="rf-scale" aria-hidden="true"><i style={{width:Math.min(100,Math.max(0,view.metrics.index))+'%'}}/><em style={{left:thresholds.attention+'%'}}/><em style={{left:thresholds.normal+'%'}}/></span>
     <span className="rf-quality-driver"><span>{qualityDriver(view.metrics)}</span><ChevronRight size={15}/></span>
    </button>
    <div className="rf-kpis">
     <div><span>В движении</span><strong>{moving}<small> из {view.trains.length}</small></strong></div>
     <div><span>Прибыли</span><strong>{view.metrics.completed_trips}<small> поездов</small></strong></div>
     <div title="По прибытиям на станции, включая ожидание"><span>Задержка</span><strong>{(view.metrics.total_delay_s/60).toFixed(1)}<small> мин</small></strong></div>
     <div className={blocked?'alert':''}><span>Открытые перегоны</span><strong>{view.sections.length-blocked}<small> из {view.sections.length}</small></strong></div>
    </div>
   </section>
   {replan&&<button className={'glass rf-ops state-'+replan.status} onClick={()=>setPanel('dispatch')}>
    <span className="rf-ops-icon">{view.replanning?<Spin size="small"/>:replan.status==='applied'?<Check size={17}/>:replan.status==='idle'?<RotateCcw size={16}/>:<TriangleAlert size={16}/>}</span>
    <span className="rf-ops-text"><strong>{replanName[replan.status]}</strong><small>Сбоев: {activeIncidents.length} · нарушений: {view.metrics.conflicts}{replan.elapsed_s!==undefined&&replan.status!=='idle'?` · ${replan.elapsed_s.toFixed(2)} с`:''}</small></span>
    <ChevronRight size={15}/>
   </button>}
   <p className="rf-advisory"><ShieldCheck size={14}/>Консультативный прототип: решения принимает диспетчер. Не заменяет СЦБ и системы безопасности движения.</p>
  </div>

  <aside className="glass rf-fleet" aria-label="Поезда">
   <div className="rf-fleet-head"><h2>Поезда <span>{view.trains.length}</span></h2></div>
   <label className="rf-search"><Search size={15}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Номер поезда" aria-label="Поиск поезда по номеру" inputMode="numeric"/>{query&&<button aria-label="Очистить поиск" onClick={()=>setQuery('')}><X size={13}/></button>}</label>
   <div className="rf-chips" role="group" aria-label="Фильтр поездов">{filters.map(f=><button key={f.key} className={filter===f.key?'on':''} aria-pressed={filter===f.key} onClick={()=>setFilter(f.key)}>{f.label}<span>{view.trains.filter(t=>matches(t,f.key)).length}</span></button>)}</div>
   <div className="rf-rows">{shownTrains.length?shownTrains.map(train=>{
    const late=train.delay_s>0;
    return <button key={train.id} className={`rf-train ${train.status}${selected===train.id?' sel':''}`} aria-pressed={selected===train.id} onClick={()=>chooseTrain(train.id)}>
     <span className={'rf-train-icon '+train.type}><span style={{transform:`rotate(${train.bearing_deg}deg)`}}>↑</span></span>
     {troubled.has(train.id)&&<TriangleAlert className="rf-train-alert" size={13} aria-label="Активный сбой"/>}
     <span className="rf-train-main"><strong>№ {train.number}</strong><small title={train.route_name}>{train.type==='passenger'?'Пасс.':'Груз.'} · → {station(train.destination_id)}</small></span>
     <span className="rf-train-side"><strong>{(train.speed_mps*3.6).toFixed(0)}<small> км/ч</small></strong><span className={'rf-state '+(late?'late':train.status)}>{late?`+${minutes(train.delay_s)}`:statusName[train.status]}</span></span>
    </button>;
   }):<p className="rf-empty">Нет поездов по этому фильтру.</p>}</div>
  </aside>

  {detailOpen&&selectedTrain&&panel==='map'&&<section className="glass rf-detail" aria-label={'Поезд № '+selectedTrain.number}>
   <div className="rf-detail-head"><span className={'rf-train-icon lg '+selectedTrain.type}><TrainFront size={18}/></span><div><h3>Поезд № {selectedTrain.number}</h3><p>{selectedTrain.type==='passenger'?'Пассажирский':'Грузовой'} · {statusName[selectedTrain.status]||selectedTrain.status}</p></div><button className="rf-close" aria-label="Закрыть карточку поезда" onClick={()=>setDetailOpen(false)}><X size={16}/></button></div>
   <RouteProgress train={selectedTrain} topology={topology}/>
   <TrainTelemetry train={selectedTrain} snapshot={view} historical={historical} topology={topology}/>
   <div className="rf-detail-actions"><Button icon={<ChartLine size={15}/>} onClick={()=>setPanel('analytics')}>Скорость и энергия</Button><Button icon={<Workflow size={15}/>} onClick={()=>setPanel('dispatch')}>Решения диспетчера</Button></div>
  </section>}

  {panel!=='map'&&<section className="glass rf-sheet" aria-label={sheet.title}>
   <div className="rf-sheet-head"><div><h2>{sheet.title}{panel==='dispatch'&&review&&<Tag color="orange">Нужен выбор</Tag>}</h2><p>{sheet.subtitle}</p></div><button className="rf-close" aria-label="Закрыть панель" onClick={()=>setPanel('map')}><X size={16}/></button></div>
   <div className="rf-sheet-body">{panel==='dispatch'?<>
    <ReplanningPanel snapshot={view} topology={topology} canControl={canControl&&!busy} historical={historical} refresh={refresh}/>
    <DispatcherPanel snapshot={view} topology={topology} preview={historical?null:preview} historical={historical} canControl={canControl&&!busy} onCalculate={calculateDispatch}/>
    <section className="panel recommendation-panel"><div className="panel-heading"><h2>Варианты расписания</h2><span className="subtle">Прогноз</span></div>{plans.length&&!historical?<div className="plan-grid">{plans.map(plan=><article key={plan.id} className={'plan-card '+(preview?.id===plan.id?'chosen':'')}><div className="plan-top"><span className="plan-check"><Check size={17}/></span><h3>{plan.label}</h3></div><div className="plan-stats"><div><span>Задержка</span><strong>{minutes(plan.metrics.total_delay_s)}</strong></div><div><span>Максимальная</span><strong>{minutes(plan.metrics.max_delay_s)}</strong></div><div><span>Энергия</span><strong>{(plan.metrics.energy_kwh/1000).toFixed(2)} МВт·ч</strong></div><div><span>Индекс прогноза</span><strong>{plan.metrics.index.toFixed(1)}</strong></div></div><div className="plan-actions"><Button onClick={()=>setPreview(plan)}>Сравнить график</Button><Button type="primary" disabled={!canControl||busy||view.replanning} onClick={()=>action('/plans/'+plan.id+'/apply')}>Применить</Button></div></article>)}</div>:<div className="empty small">{historical?'Вернитесь в эфир для расчёта.':view.replanning?'Рассчитываем варианты…':'Нажмите «Пауза и расчёт», чтобы сравнить варианты расписания.'}</div>}</section>
    {preview&&!historical&&<section className="panel diagram-panel"><div className="panel-heading"><h2>{preview.label}</h2><Button onClick={()=>setPreview(null)}>Закрыть сравнение</Button></div><PlanComparisonPanel planId={preview.id} snapshot={view} topology={topology} connected={connected&&!stale}/><div className="comparison-chart-legend">┄ Действующий план · ━ Выбранный вариант</div><TrainChart topology={topology} snapshot={view} baseline={view.plan} preview={preview}/></section>}
   </>:<div className="analytics-grid"><section className="panel diagram-panel"><div className="panel-heading"><h2>График движения</h2><div className="chart-legend"><span>┄ Исходный</span><span>━ Активный</span></div></div><TrainChart topology={topology} snapshot={view} baseline={baseline} preview={null}/></section><section className="panel speed-panel"><div className="panel-heading"><h2>Скорость и энергия</h2><Select aria-label="Поезд для рекомендаций" value={selected} onChange={select} options={view.trains.map(t=>({value:t.id,label:'№ '+t.number}))}/></div><SpeedAdvicePanel train={selectedTrain} snapshot={view} topology={topology} profile={profile} historical={historical} canControl={canControl&&!busy} refresh={refresh}/></section><QualityPanel snapshot={view} topology={topology} historical={historical} active={panel==='analytics'} connected={connected&&!stale}/></div>}</div>
  </section>}

  {!historical&&<div className="glass clear rf-controls" role="toolbar" aria-label="Управление симуляцией">
   <Button type="primary" className="rf-run" disabled={!canControl} loading={busy} icon={view.running?<Pause size={15}/>:<Play size={15}/>} onClick={()=>action('/simulation/'+(view.running?'pause':'start'))}>{view.running?'Пауза':'Запустить'}</Button>
   <Tooltip title="Сбросить сценарий"><Button shape="circle" aria-label="Сброс" disabled={!canControl||busy} icon={<RotateCcw size={15}/>} onClick={()=>modal.confirm({title:'Сбросить сценарий?',content:'Движение вернётся к исходному состоянию.',okText:'Сбросить',cancelText:'Отмена',onOk:()=>action('/simulation/reset').then(()=>{setPreview(null);setArchive(null);})})}/></Tooltip>
   <span className="rf-sep"/>
   <div className="rf-speed" role="group" aria-label="Скорость времени модели"><span>Время</span>{speedPresets.map(multiplier=><button key={multiplier} className={view.speed===multiplier?'on':''} aria-pressed={view.speed===multiplier} disabled={!canControl||busy} onClick={()=>action('/simulation/speed',{multiplier})}>{multiplier}×</button>)}<SpeedControl value={view.speed} disabled={!canControl||busy} onApply={multiplier=>action('/simulation/speed',{multiplier})}/></div>
   <span className="rf-sep"/>
   <Button className="rf-incident" disabled={!canControl||busy} icon={<TriangleAlert size={15}/>} onClick={()=>setIncidentOpen(true)}>Добавить сбой</Button>
  </div>}

  <div className="glass clear rf-mapdock" role="toolbar" aria-label="Вид карты">
   <div ref={segRef} className="rf-seg rf-lens"><button className={mapTab==='map'?'on':''} aria-pressed={mapTab==='map'} onClick={()=>setMapTab('map')}>География</button><button className={mapTab==='scheme'?'on':''} aria-pressed={mapTab==='scheme'} onClick={()=>setMapTab('scheme')}>Схема путей</button><button className={mapTab==='3d'?'on':''} aria-pressed={mapTab==='3d'} onClick={()=>setMapTab('3d')}>3D-вид</button></div>
   {mapTab==='3d'&&<div ref={cameraRef} className="rf-seg rf-lens" role="group" aria-label="Фиксированная камера" title="Камера следует за выбранным поездом; колесо мыши меняет масштаб">{(['2d','3d'] as const).map(m=><button key={m} className={camera===m?'on':''} aria-pressed={camera===m} onClick={()=>setCamera(m)}>{m==='2d'?'2D сверху':'3D'}</button>)}</div>}
   {mapTab==='map'&&<button className="rf-dock-btn" onClick={()=>setAllCountry(x=>!x)} aria-label={allCountry?'К маршруту':'Весь Казахстан'}><Earth size={15}/><span>{allCountry?'К маршруту':'Весь Казахстан'}</span></button>}
   <button className={'rf-dock-btn icon'+(legendOpen?' on':'')} aria-label="Легенда карты" aria-expanded={legendOpen} onClick={()=>setLegendOpen(x=>!x)}><Info size={16}/></button>
  </div>
  {legendOpen&&<div className="glass rf-legend" role="dialog" aria-label="Легенда карты">
   <div className="rf-legend-head"><h4>Легенда</h4><button className="rf-close sm" aria-label="Закрыть легенду" onClick={()=>setLegendOpen(false)}><X size={14}/></button></div>
   <ul>
    <li><i className="line corridor"/>Моделируемый участок</li>
    <li><i className="line occupied"/>Перегон занят поездом</li>
    <li><i className="line signal"/>Неисправность сигнала</li>
    <li><i className="line closed"/>Перегон закрыт</li>
    <li><i className="line network"/>Сеть Казахстана, OSM</li>
    <li><i className="pin passenger"/>Пассажирский поезд</li>
    <li><i className="pin freight"/>Грузовой поезд</li>
    <li><i className="pin stopped"/>Пунктир — поезд стоит</li>
   </ul>
   <p>Стрелка показывает направление движения. Геометрия — OpenStreetMap (ODbL), станции и сигналы — демомодель.</p>
  </div>}

  <Drawer title="Индекс качества движения" open={qualityOpen} onClose={()=>setQualityOpen(false)} width={520}><QualityDetails metrics={view.metrics} topology={topology}/></Drawer>
  <Modal title="Добавить сбой" open={incidentOpen} onCancel={()=>setIncidentOpen(false)} onOk={createIncident} confirmLoading={busy} okText="Добавить и пересчитать" cancelText="Отмена" okButtonProps={{disabled:!incidentTarget||!canControl}}><div className="modal-fields"><label>Тип события<Select value={incidentKind} onChange={changeIncidentKind} options={Object.entries(incidentName).map(([value,label])=>({value,label}))}/></label><label>{incidentKind==='delay'?'Поезд на станции':'Перегон'}<Select value={incidentTarget||undefined} onChange={setIncidentTarget} options={incidentKind==='delay'?view.trains.filter(t=>t.status==='waiting').map(t=>({value:t.id,label:`№ ${t.number}`})):topology.sections.map((s,i)=>({value:s.id,label:`${topology.stations[i].name} — ${topology.stations[i+1].name}`}))}/></label><label>Длительность, минут<InputNumber value={duration} min={1} max={120} onChange={n=>setDuration(n||10)}/></label><p className="subtle">Поезда, уже вошедшие на перегон, освобождают его. Новые отправления удерживаются до применения проверенного плана.</p></div></Modal>
  {snapshot&&<HistoryPanel live={snapshot} topology={topology} open={historyOpen} onClose={()=>setHistoryOpen(false)} onView={showArchive}/>}
  <SettingsDrawer open={settingsOpen} epoch={snapshot?.epoch||view.epoch} canEdit={auth?.role==='admin'&&canControl} onClose={()=>setSettingsOpen(false)} onSaved={refresh}/>
 </div>;
}
