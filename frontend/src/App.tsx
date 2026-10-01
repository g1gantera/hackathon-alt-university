import {useCallback,useEffect,useState} from 'react';
import {App as AntApp,Button,Drawer,Form,Input,InputNumber,Modal,Select,Spin,Tag,Dropdown,Tabs} from 'antd';
import {ArrowRight,Check,ChevronRight,Clock3,MapPin,MoreHorizontal,Pause,Play,RotateCcw,TrainFront,TriangleAlert} from 'lucide-react';
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
import type {Plan,Profile,Snapshot,Topology} from './types';

const statusName:Record<string,string>={waiting:'На станции',moving:'В движении',completed:'Прибыл'};
const incidentName:Record<string,string>={delay:'Задержка поезда',closure:'Закрытие перегона',signal:'Неисправность сигнала'};

export default function App(){
 const {message,modal}=AntApp.useApp();
 const {snapshot,selected,select,connected,lastUpdate,setSnapshot,setConnected}=useDispatch();
 const [topology,setTopology]=useState<Topology|null>(null),[baseline,setBaseline]=useState<Plan|null>(null),[plans,setPlans]=useState<Plan[]>([]),[preview,setPreview]=useState<Plan|null>(null);
 const [profile,setProfile]=useState<Profile|null>(null),[allCountry,setAllCountry]=useState(false),[mapTab,setMapTab]=useState('map');
 const [workspaceTab,setWorkspaceTab]=useState('overview'),[qualityOpen,setQualityOpen]=useState(false);
 const [incidentOpen,setIncidentOpen]=useState(false),[incidentKind,setIncidentKind]=useState('closure'),[incidentTarget,setIncidentTarget]=useState('section-1'),[duration,setDuration]=useState(10);
 const [settingsOpen,setSettingsOpen]=useState(false),[historyOpen,setHistoryOpen]=useState(false),[archive,setArchive]=useState<Snapshot|null>(null);
 const [auth,setAuth]=useState<{role:string;demo:boolean}|null>(null),[loginNeeded,setLoginNeeded]=useState(false),[fatal,setFatal]=useState(''),[busy,setBusy]=useState(false),[stale,setStale]=useState(false);
 const view=archive??snapshot;
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
 const action=async(path:string,body?:unknown)=>{setBusy(true);try{await api(path,'POST',body);await refresh();}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 const calculateDispatch=async()=>{setBusy(true);try{await api('/simulation/pause','POST');await api('/replan','POST');await refresh();}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};

 const openSettings=()=>setSettingsOpen(true);
 const createIncident=async()=>{setBusy(true);try{await api('/incidents','POST',{kind:incidentKind,target_id:incidentTarget,duration_s:duration*60});setIncidentOpen(false);setPlans([]);await refresh();message.success('Сбой зарегистрирован. Новые отправления удерживаются до применения плана.');}catch(e){message.error((e as Error).message);}finally{setBusy(false);}};
 const changeIncidentKind=(kind:string)=>{setIncidentKind(kind);setIncidentTarget(kind==='delay'?view?.trains.find(t=>t.status==='waiting')?.id||'':'section-1');};

 if(loginNeeded)return <div className="auth-screen"><div className="auth-card"><TrainFront size={40}/><h1>RailFlow</h1><p>Вход в диспетчерский центр</p><Form layout="vertical" initialValues={{role:'viewer'}} onFinish={async values=>{try{await api('/auth/login','POST',values);setAuth(await api('/auth/me'));setLoginNeeded(false);}catch(e){message.error((e as Error).message);}}}><Form.Item name="role" label="Роль"><Select options={[{value:'viewer',label:'Наблюдатель'},{value:'dispatcher',label:'Диспетчер'},{value:'admin',label:'Администратор'}]}/></Form.Item><Form.Item name="password" label="Пароль" rules={[{required:true}]}><Input.Password/></Form.Item><Button htmlType="submit" type="primary" block>Войти</Button></Form></div></div>;
 if(!view||!topology)return <div className="loading"><TrainFront size={44}/><h2>RailFlow</h2><Spin/><p>{fatal||'Подготавливаем участок Астана — Кокшетау…'}</p>{fatal&&<Button onClick={()=>location.reload()}>Повторить подключение</Button>}</div>;

 const moving=view.trains.filter(t=>t.status==='moving').length;
 const blocked=view.metrics.blocked_sections?.length??view.sections.filter(s=>['closed','signal_failure'].includes(s.status)).length;
 const holdNotice=view.replanning?'Отправления удерживаются до завершения расчёта.':view.replan_status?.status==='failed'?'Расчёт не завершён. Устраните сбой или повторите попытку.':'Отправления удерживаются. Примените новый план.';
 const quality=qualityStatus(view.metrics);
 return <div className="shell clean-dashboard"><div className="workspace">
  <header className="topbar">
   <div className="wordmark"><TrainFront size={24}/> RailFlow</div>
   <span className="demo-tag">{auth?.demo?'СИМУЛЯЦИЯ':auth?.role}</span>
   <div className="connection"><span className={'dot '+(connected&&!stale?'green':'red')}/>{connected&&!stale?'В эфире':'Нет свежих данных'}</div>
   <Dropdown trigger={['click']} menu={{items:[{key:'history',label:'История и отчёт CSV'},{key:'settings',label:'Настройки сценария'},{key:'demo',label:<a href="/stage3.html">Отдельное демо автодиспетчера ↗</a>},...(!auth?.demo?[{key:'logout',label:'Выйти'}]:[])],onClick:({key})=>{if(key==='history')setHistoryOpen(true);if(key==='settings')void openSettings();if(key==='logout')void api('/auth/logout','POST').then(()=>location.reload());}}}><Button icon={<MoreHorizontal size={18}/>} aria-label="Дополнительные инструменты">Ещё</Button></Dropdown>
  </header>
  <main>
   <section className="page-heading"><div><h1>Астана — Кокшетау</h1><p>{topology.stations.length} станций · {(topology.length_m/1000).toFixed(1)} км</p></div><span className="date-pill"><Clock3 size={16}/> {clock(view.sim_time_s)} <small>время модели</small></span></section>
   {(historical||view.awaiting_plan||!connected||stale)&&<div className={'notice '+(historical?'history-notice':'')} role="status"><TriangleAlert size={16}/>{historical?'Архив: показан сохранённый снимок. Управление отключено.':!connected||stale?'Нет свежих данных. Переподключаемся…':holdNotice}{historical?<Button size="small" onClick={()=>setHistoryOpen(true)}>История и отчёт</Button>:view.replan_status?.status==='review'&&<Button size="small" onClick={()=>setWorkspaceTab('dispatch')}>Открыть варианты</Button>}</div>}
   <section className="metrics-grid">
    <button className={'metric featured quality-'+quality.tone} onClick={()=>setQualityOpen(true)} aria-label={'Индекс качества движения '+view.metrics.index.toFixed(1)+' из 100. '+quality.label+'. Подробнее'}><div className="metric-label">Качество движения <Tag color={quality.color}>{quality.label}</Tag></div><div className="metric-value" key={paintIdentifier(view,'dashboard')??'quality'} {...paintAttributes(view,'dashboard')}>{view.metrics.index.toFixed(1)}<span>/ 100</span></div><div className="metric-foot">{qualityDriver(view.metrics)} <ChevronRight size={13}/></div><div className="meter"><i style={{width:view.metrics.index+'%'}}/></div></button>
    <div className="metric"><div className="metric-label">Поезда в движении</div><div className="metric-value">{moving}<span>из {view.trains.length}</span></div><div className="metric-foot">{view.metrics.completed_trips} завершили маршрут</div></div>
    <div className="metric"><div className="metric-label">Накопленная задержка</div><div className="metric-value">{(view.metrics.total_delay_s/60).toFixed(1)}<span>мин</span></div><div className="metric-foot">По прибытиям на станции · включая ожидание</div></div>
    <div className={'metric '+(blocked?'metric-alert':'')}><div className="metric-label">Открытые перегоны</div><div className="metric-value">{view.sections.length-blocked}<span>из {view.sections.length}</span></div><div className="metric-foot">Нарушений расписания: {view.metrics.conflicts}</div></div>
   </section>
   <section className="control-bar">
    <Button type="primary" disabled={!canControl} loading={busy} icon={view.running?<Pause size={15}/>:<Play size={15}/>} onClick={()=>action('/simulation/'+(view.running?'pause':'start'))}>{view.running?'Пауза':'Запустить'}</Button>
    <Button disabled={!canControl||busy} icon={<RotateCcw size={15}/>} onClick={()=>modal.confirm({title:'Сбросить сценарий?',content:'Движение вернётся к исходному состоянию.',okText:'Сбросить',cancelText:'Отмена',onOk:()=>action('/simulation/reset').then(()=>{setPreview(null);setArchive(null);})})}>Сброс</Button>
    <div className="simulation-speed"><span>Скорость времени</span><SpeedControl value={view.speed} disabled={!canControl||busy} onApply={multiplier=>action('/simulation/speed',{multiplier})}/></div>
    <Button className="incident-button" disabled={!canControl||busy} icon={<TriangleAlert size={15}/>} onClick={()=>setIncidentOpen(true)}>Добавить сбой</Button>
   </section>
   <ReplanningPanel snapshot={view} topology={topology} canControl={canControl&&!busy} historical={historical} refresh={refresh}/>
   <Tabs activeKey={workspaceTab} onChange={setWorkspaceTab} items={[
    {key:'overview',label:'Карта',children:<div className="primary-grid"><div className="left-stack">
     <section className="panel map-panel"><div className="panel-heading"><div className="panel-title"><MapPin size={17}/><h2>Карта движения</h2><span className="live-badge">{historical?'АРХИВ':'LIVE'}</span></div><div className="tab-buttons"><button className={mapTab==='map'?'on':''} onClick={()=>setMapTab('map')}>География</button><button className={mapTab==='scheme'?'on':''} onClick={()=>setMapTab('scheme')}>Схема путей</button></div></div><div className="map-toolbar"><span><i className="legend-line green-line"/> Выбранный участок <i className="legend-line gray-line"/> Сеть Казахстана</span>{mapTab==='map'&&<button onClick={()=>setAllCountry(x=>!x)}>{allCountry?'К маршруту':'Весь Казахстан'} <ArrowRight size={13}/></button>}</div>{mapTab==='map'?<RailMap topology={topology} snapshot={view} allCountry={allCountry}/>:<TrackDiagram topology={topology} snapshot={view}/>}<div className="map-footer"><span><span className="dot green"/> Пассажирский</span><span><span className="dot amber"/> Грузовой</span><span><span className="dot red"/> Закрытие / запрет</span><span className="map-model">Геометрия OSM · инфраструктура демо</span></div></section>

     <section className="panel detail-panel"><div className="panel-heading"><h2>Поезд № {selectedTrain?.number}</h2><Tag color="cyan">{selectedTrain&&statusName[selectedTrain.status]}</Tag></div><TrainTelemetry train={selectedTrain} snapshot={view} historical={historical} topology={topology}/></section>
    </div><aside className="right-stack">
     <section className="panel fleet-panel"><div className="panel-heading"><div className="panel-title"><TrainFront size={17}/><h2>Поезда</h2></div><span className="count-badge">{view.trains.length}</span></div><div className="fleet-list">{view.trains.map(train=><button key={train.id} className={`train-row ${selected===train.id?'selected':''}`} onClick={()=>select(train.id)}><span className={`train-icon ${train.type}`}><TrainFront size={17}/></span><span className="train-ident"><strong>№ {train.number} <small>{train.direction>0?'↗':'↙'}</small></strong><span>{train.type==='passenger'?'Пассажирский':'Грузовой'}</span></span><span className="train-speed"><strong>{(train.speed_mps*3.6).toFixed(0)} <small>км/ч</small></strong><span className={train.delay_s?'late':''}>{train.delay_s?`+${minutes(train.delay_s)}`:statusName[train.status]}</span></span><ChevronRight size={13}/></button>)}</div></section>

    </aside></div>},
    {key:'dispatch',label:<>Диспетчер {view.replan_status?.status==='review'&&<Tag color="orange">Нужен выбор</Tag>}</>,children:<>
     <DispatcherPanel snapshot={view} topology={topology} preview={historical?null:preview} historical={historical} canControl={canControl&&!busy} onCalculate={calculateDispatch}/>
     <section className="panel recommendation-panel"><div className="panel-heading"><h2>Варианты расписания</h2><span className="subtle">Прогноз</span></div>{plans.length&&!historical?<div className="plan-grid">{plans.map(plan=><article key={plan.id} className={'plan-card '+(preview?.id===plan.id?'chosen':'')}><div className="plan-top"><span className="plan-check"><Check size={17}/></span><h3>{plan.label}</h3></div><div className="plan-stats"><div><span>Задержка</span><strong>{minutes(plan.metrics.total_delay_s)}</strong></div><div><span>Максимальная</span><strong>{minutes(plan.metrics.max_delay_s)}</strong></div><div><span>Энергия</span><strong>{(plan.metrics.energy_kwh/1000).toFixed(2)} МВт·ч</strong></div><div><span>Индекс прогноза</span><strong>{plan.metrics.index.toFixed(1)}</strong></div></div><div className="plan-actions"><Button onClick={()=>setPreview(plan)}>Сравнить график</Button><Button type="primary" disabled={!canControl||busy||view.replanning} onClick={()=>action('/plans/'+plan.id+'/apply')}>Применить</Button></div></article>)}</div>:<div className="empty small">{historical?'Вернитесь в эфир для расчёта.':view.replanning?'Рассчитываем варианты…':'Нажмите «Пауза и расчёт», чтобы сравнить варианты расписания.'}</div>}</section>
     {preview&&!historical&&<section className="panel diagram-panel"><div className="panel-heading"><h2>{preview.label}</h2><Button onClick={()=>setPreview(null)}>Закрыть сравнение</Button></div><PlanComparisonPanel planId={preview.id} snapshot={view} topology={topology} connected={connected&&!stale}/><div className="comparison-chart-legend">┄ Действующий план · ━ Выбранный вариант</div><TrainChart topology={topology} snapshot={view} baseline={view.plan} preview={preview}/></section>}
    </>},
    {key:'analytics',label:'Аналитика',children:<div className="analytics-grid"><section className="panel diagram-panel"><div className="panel-heading"><h2>График движения</h2><div className="chart-legend"><span>┄ Исходный</span><span>━ Активный</span></div></div><TrainChart topology={topology} snapshot={view} baseline={baseline} preview={null}/></section><section className="panel speed-panel"><div className="panel-heading"><h2>Скорость и энергия</h2><Select aria-label="Поезд для рекомендаций" value={selected} onChange={select} options={view.trains.map(t=>({value:t.id,label:'№ '+t.number}))}/></div><SpeedAdvicePanel train={selectedTrain} snapshot={view} topology={topology} profile={profile} historical={historical} canControl={canControl&&!busy} refresh={refresh}/></section><QualityPanel snapshot={view} topology={topology} historical={historical} active={workspaceTab==='analytics'} connected={connected&&!stale}/></div>},
   ]}/>
   <footer className="footer"><span>RailFlow · Диспетчерский симулятор</span><span>© OpenStreetMap contributors · ODbL</span></footer>
  </main>
 </div>
 <Drawer title="Индекс качества движения" open={qualityOpen} onClose={()=>setQualityOpen(false)} width={520}><QualityDetails metrics={view.metrics} topology={topology}/></Drawer>
  <Modal title="Добавить сбой" open={incidentOpen} onCancel={()=>setIncidentOpen(false)} onOk={createIncident} confirmLoading={busy} okText="Добавить и пересчитать" cancelText="Отмена" okButtonProps={{disabled:!incidentTarget||!canControl}}><div className="modal-fields"><label>Тип события<Select value={incidentKind} onChange={changeIncidentKind} options={Object.entries(incidentName).map(([value,label])=>({value,label}))}/></label><label>{incidentKind==='delay'?'Поезд на станции':'Перегон'}<Select value={incidentTarget||undefined} onChange={setIncidentTarget} options={incidentKind==='delay'?view.trains.filter(t=>t.status==='waiting').map(t=>({value:t.id,label:`№ ${t.number}`})):topology.sections.map((s,i)=>({value:s.id,label:`${topology.stations[i].name} — ${topology.stations[i+1].name}`}))}/></label><label>Длительность, минут<InputNumber value={duration} min={1} max={120} onChange={n=>setDuration(n||10)}/></label><p className="subtle">Поезда, уже вошедшие на перегон, освобождают его. Новые отправления удерживаются до применения проверенного плана.</p></div></Modal>
  {snapshot&&<HistoryPanel live={snapshot} topology={topology} open={historyOpen} onClose={()=>setHistoryOpen(false)} onView={showArchive}/>}
  <SettingsDrawer open={settingsOpen} epoch={snapshot?.epoch||view.epoch} canEdit={auth?.role==='admin'&&canControl} onClose={()=>setSettingsOpen(false)} onSaved={refresh}/>
 </div>;
}
