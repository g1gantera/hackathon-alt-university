import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createArticulatedTrain, createStation, createSignal, createSwitchMarker, createTree, disposeObject, type RailConsist} from './assets.ts';
import {decodeNetwork, project, projectLatLon, createRouteSampler, routeSignature, signalPose, vehiclePose,
  canInterpolateTrain, toMotionFrame, type EraSnapshot, type EraTrain, type RailNetwork, type RouteSampler} from './adapter.ts';
import {TrainMotionBuffer} from './motion.ts';
import {buildTrackDetail} from './track.ts';

declare global {
 interface Window {RailI18n?:{t:(source:string,params?:Record<string,string|number>)=>string;message:(source:string)=>string;language:string;setLanguage:(language:string,persist?:boolean)=>boolean;translate:()=>void}}
}
const t=(source:string,params?:Record<string,string|number>)=>window.RailI18n?.t(source,params)??source;
const localizedNumber=(value:number)=>value.toLocaleString(window.RailI18n?.language==='kk'?'kk-KZ':window.RailI18n?.language==='ru'?'ru-RU':'en-US');
const $ = <T extends HTMLElement = HTMLElement>(id:string) => document.getElementById(id) as T;
const origin = location.origin;
const post = (message:object) => {if(parent !== window) parent.postMessage(message,origin);};
type StateMessage = {type:'state';state:EraSnapshot;selected:string|null;replay:boolean};
type Path = {points:[number,number][];category:string;edge:number;minX:number;maxX:number;minZ:number;maxZ:number};
type TrainView = {model:RailConsist;label:HTMLElement;sampler:RouteSampler|null;motion:TrainMotionBuffer;train:EraTrain;length:number;head:THREE.Vector3};
let pending:StateMessage|null = null, receive:((data:StateMessage)=>void)|null = null;
let focus:()=>void = ()=>{}, toggleLayers:()=>void = ()=>{};
let refreshLanguage:()=>void=()=>{},activeError:string|null=null;
window.addEventListener('message',event=>{
  if(event.origin!==origin||event.source!==parent||!event.data)return;
  if(event.data.type==='state'){pending=event.data;if(receive)receive(event.data);}
  if(event.data.type==='focus')focus();
  if(event.data.type==='layers')toggleLayers();
});

async function main(){
 try {if(parent!==window&&parent.RailI18n)window.RailI18n?.setLanguage(parent.RailI18n.language,false);}catch{/* The parent may be cross-origin when opened externally. */}
 window.RailI18n?.translate();
 refreshLanguage=()=>{$('scene-title').textContent=t('Railway network');$('scene-status').textContent=t('Loading track geometry…');$('follow').textContent=t('Follow train');};
 refreshLanguage();
 window.addEventListener('railflow:languagechange',()=>{refreshLanguage();if(activeError)showError(activeError);});
 const renderer = new THREE.WebGLRenderer({antialias:true,alpha:false,logarithmicDepthBuffer:true,powerPreference:'high-performance'});
 renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
 renderer.setClearColor(0xe9eee7);
 renderer.outputColorSpace = THREE.SRGBColorSpace;
 renderer.toneMapping = THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;
 $('viewport').appendChild(renderer.domElement);
 const scene=new THREE.Scene();scene.background=new THREE.Color(0xe9eee7);
 const camera=new THREE.PerspectiveCamera(42,1,.5,2000000);
 const controls=new OrbitControls(camera,renderer.domElement);
 controls.enableDamping=true;controls.dampingFactor=.11;controls.maxPolarAngle=Math.PI*.47;
 controls.minDistance=24;controls.maxDistance=3500000;
 scene.add(new THREE.HemisphereLight(0xffffff,0x829e87,2.5));
 const sun=new THREE.DirectionalLight(0xfff3d5,3);sun.position.set(-500,1000,300);scene.add(sun);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.MeshStandardMaterial({color:0xe0e8dc,roughness:1}));
 ground.rotation.x=-Math.PI/2;ground.position.y=-.45;scene.add(ground);
 const tracks=new THREE.Group(),stations=new THREE.Group(),signalGroup=new THREE.Group(),authority=new THREE.Group();
 scene.add(tracks,stations,signalGroup,authority);
 let network:RailNetwork,paths:Path[]=[],detail:THREE.Group|null=null;
 let snapshot:EraSnapshot|null=null,selected:string|null=null,replay=false,follow=false,dirty=true;
 let acknowledged=-1,frameCount=0,lastTime=performance.now(),lastDetailAt=0,initialFocus=false;
 let detailCenter=new THREE.Vector2(Infinity,Infinity),detailRadius=0;
 const trainViews=new Map<string,TrainView>();
 const stationLabels:{element:HTMLElement;position:THREE.Vector3}[]=[];
 const signalModels=new Map<string,THREE.Group>();
 const switchModels=new Map<number,THREE.Group>();
 const layerState={stations:true,signals:true,authority:true,labels:true};
 const vector=new THREE.Vector3();
 const metrics={frames:0,drawCalls:0,triangles:0,lastVersion:-1,modelTrains:0};
 Object.assign(window,{railSceneMetrics:metrics});
 const resize=()=>{const w=innerWidth,h=innerHeight;if(!w||!h)return;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();dirty=true;};
 new ResizeObserver(resize).observe(document.body);resize();
 controls.addEventListener('change',()=>{dirty=true;});
 controls.addEventListener('start',()=>{follow=false;updateFollow();});
 renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();showError('The graphics context was interrupted. Switch to 2D, then reopen 3D to reload the view.');});
 toggleLayers=()=>{$('layers').hidden=!$('layers').hidden;};
 document.querySelectorAll<HTMLInputElement>('[data-layer]').forEach(input=>input.addEventListener('change',()=>{
   const key=input.dataset.layer as keyof typeof layerState;layerState[key]=input.checked;
   stations.visible=layerState.stations;signalGroup.visible=layerState.signals;authority.visible=layerState.authority;
   $('labels').hidden=!layerState.labels;dirty=true;
 }));
 function updateFollow(){$('follow').setAttribute('aria-pressed',String(follow));$('follow').textContent=t(follow?'Following train':'Follow train');}
 const activeId=()=>selected&&trainViews.has(selected)?selected:trainViews.keys().next().value||'';
 $('follow').onclick=()=>{if(!trainViews.size)return;follow=!follow;updateFollow();if(follow)focusTrain(activeId());dirty=true;};
 $('fullscreen').onclick=()=>{const action=document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();action.catch(()=>{});};
 function cameraAt(x:number,z:number,distance:number){controls.target.set(x,0,z);camera.position.set(x+distance*.34,distance*.72,z+distance*.62);controls.update();dirty=true;}
 function focusTrain(id:string,whole=false){const view=trainViews.get(id);if(!view)return;
  const chainage=view.motion.sample(id,performance.now())?.position??view.train.distance_m;
  const pose=view.sampler?.(chainage-(whole?view.length/2:8),true);
  const [x,z]=pose?[pose.x,pose.z]:projectLatLon(view.train.position);
  cameraAt(x,z,whole?Math.max(160,view.length*2):Math.max(80,Math.min(180,view.length*.75)));
 }
 $('whole-train').onclick=()=>{follow=false;updateFollow();focusTrain(activeId(),true);};
 focus=()=>{
  follow=false;updateFollow();
  if(selected&&trainViews.has(selected)){focusTrain(selected);return;}
  const points=snapshot?.trains.filter(t=>t.state!=='completed').map(t=>projectLatLon(t.position))||[];
  if(!points.length){cameraAt(...project([69.4,53.28]),1800);return;}
  const bounds=new THREE.Box2();points.forEach(p=>bounds.expandByPoint(new THREE.Vector2(...p)));
  const center=bounds.getCenter(new THREE.Vector2()),size=bounds.getSize(new THREE.Vector2());
  cameraAt(center.x,center.y,Math.max(800,Math.max(size.x,size.y)*1.7));
 };
 $('overview').onclick=()=>{follow=false;updateFollow();const bounds=new THREE.Box2();paths.forEach(p=>{bounds.expandByPoint(new THREE.Vector2(p.minX,p.minZ));bounds.expandByPoint(new THREE.Vector2(p.maxX,p.maxZ));});const c=bounds.getCenter(new THREE.Vector2()),s=bounds.getSize(new THREE.Vector2());cameraAt(c.x,c.y,Math.max(s.x,s.y)*.95);};
 cameraAt(...project([69.4,53.28]),1800);
 const response=await fetch('network.json.gz');if(!response.ok||!response.body)throw new Error('Track geometry could not be loaded.');
 const raw=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
 network=decodeNetwork(raw);
 const sectionCount=network.edges.filter(e=>!e.synthetic).length;
 const stationPlaces=new Map<string,typeof network.stations[number]>();
 for(const station of network.stations){if(!station.name&&!station.name_en||station.type==='tram_stop')continue;
  const label=`${station.name_en||station.name}${station.name_en&&station.name_en!==station.name?' / '+station.name:''} · ${station.id}`;
  stationPlaces.set(label,station);const option=document.createElement('option');option.value=label;$('station-places').append(option);
 }
 $('station-search').addEventListener('change',()=>{const station=stationPlaces.get($<HTMLInputElement>('station-search').value);if(!station)return;
  follow=false;updateFollow();const anchor=station.vertex===null?station.coordinate:network.vertices[station.vertex];cameraAt(...project(anchor),220);
 });
 const adjacency:number[][]=network.vertices.map(()=>[]);
 const overviewBatches=new Map<string,number[]>();
 for(const edge of network.edges){
  // Synthetic inferred connections stay excluded, as in era's routable graph.
  if(edge.synthetic||['tram','subway','light_rail','narrow_gauge'].includes(edge.category))continue;
  adjacency[edge.u].push(edge.id);adjacency[edge.v].push(edge.id);
  const points=edge.geometry.map(project),xs=points.map(p=>p[0]),zs=points.map(p=>p[1]);
  paths.push({points,edge:edge.id,category:edge.category,minX:Math.min(...xs),maxX:Math.max(...xs),minZ:Math.min(...zs),maxZ:Math.max(...zs)});
  const category=edge.category==='main'?'main':edge.category==='siding'||edge.category==='yard'?'service':'other';
  const positions=overviewBatches.get(category)||[];
  for(let i=1;i<points.length;i++)positions.push(points[i-1][0],.02,points[i-1][1],points[i][0],.02,points[i][1]);
  overviewBatches.set(category,positions);
 }
 for(const [category,positions] of overviewBatches){const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));tracks.add(new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:category==='main'?0x618479:category==='service'?0xa8a788:0x82968b})));}
 // All station locations remain visible in the national overview, without thousands of model draw calls.
 const stationDotsGeometry=new THREE.BufferGeometry();stationDotsGeometry.setAttribute('position',new THREE.Float32BufferAttribute(network.stations.filter(s=>s.type!=='tram_stop').flatMap(s=>{const [x,z]=project(s.coordinate);return [x,1,z];}),3));
 const stationDots=new THREE.Points(stationDotsGeometry,new THREE.PointsMaterial({color:0x46786d,size:3,sizeAttenuation:false}));stations.add(stationDots);
 const stationBuildings=new THREE.Group();stations.add(stationBuildings);
 function clearGroup(group:THREE.Group){for(const child of [...group.children]){
   // Model resources are cached, whereas per-snapshot authority lines are owned.
   if(child instanceof THREE.LineSegments){child.geometry.dispose();const materials=Array.isArray(child.material)?child.material:[child.material];materials.forEach(material=>material.dispose());}
   group.remove(child);disposeObject(child);
 }}
 function updateDetail(now:number){
  const distance=camera.position.distanceTo(controls.target);
  // Hysteresis keeps wheel/sleeper LOD stable near zoom boundaries.
  const radius=distance<(detailRadius===600?500:420)?600:distance<(detailRadius===0?6000:7000)?1500:0;
  const center=new THREE.Vector2(controls.target.x,controls.target.z);
  if(radius===detailRadius&&center.distanceTo(detailCenter)<Math.max(120,radius*.28))return;
  if(now-lastDetailAt<250)return;
  lastDetailAt=now;detailRadius=radius;detailCenter.copy(center);
  const nearby=radius?paths.filter(p=>p.minX<center.x+radius&&p.maxX>center.x-radius&&p.minZ<center.y+radius&&p.maxZ>center.y-radius):[];
  const next=radius?buildTrackDetail(nearby,[center.x,center.y],radius):null;
  if(next)scene.add(next);
  if(detail){scene.remove(detail);detail.userData.dispose?.();}detail=next;
  clearGroup(stationBuildings);stationLabels.forEach(l=>l.element.remove());stationLabels.length=0;
  if(radius)for(const station of network.stations.filter(s=>s.type!=='tram_stop').filter(s=>{const p=project(s.coordinate);return Math.hypot(p[0]-center.x,p[1]-center.y)<radius;}).slice(0,48)){
   const vertex=station.vertex,edge=vertex===null?null:network.edges[adjacency[vertex]?.[0]];
   let [x,z]=project(vertex!==null?network.vertices[vertex]:station.coordinate),angle=0;
   if(edge){const index=vertex===edge.v?edge.geometry.length-2:0;const a=project(edge.geometry[index]),b=project(edge.geometry[index+1]);angle=Math.atan2(b[0]-a[0],b[1]-a[1]);}
   const model=createStation(station.type==='halt'||station.type==='stop'?'halt':/Астана|Astana|Кокшетау|Kokshetau/i.test(station.name+' '+station.name_en)?'terminal':'station');
   model.position.set(x+Math.cos(angle)*8,.04,z-Math.sin(angle)*8);model.rotation.y=angle;stationBuildings.add(model);
   const label=document.createElement('div');label.className='label station';label.textContent=station.name_en||station.name||t('Rail stop');if(!station.name_en&&!station.name)label.dataset.i18n='Rail stop';$('labels').append(label);
   stationLabels.push({element:label,position:new THREE.Vector3(x,18,z)});
   // Decorative planting around buildings; never interpreted as graph assets.
   for(let i=0;i<3;i++){const tree=createTree();tree.position.set(x+Math.cos(angle)*(31+i*6)+Math.sin(angle)*18,0,z-Math.sin(angle)*(31+i*6)+Math.cos(angle)*18);stationBuildings.add(tree);}
  }
  for(const [vertex,model] of switchModels){signalGroup.remove(model);disposeObject(model);switchModels.delete(vertex);}
  if(radius)for(const edge of nearby){const rawEdge=network.edges[edge.edge];for(const vertex of [rawEdge.u,rawEdge.v]){if(switchModels.has(vertex)||adjacency[vertex].length<3||switchModels.size>=160)continue;const [x,z]=project(network.vertices[vertex]);if(Math.hypot(x-center.x,z-center.y)>radius)continue;const marker=createSwitchMarker();marker.position.set(x+3,.05,z);signalGroup.add(marker);switchModels.set(vertex,marker);}}
  updateSignals();dirty=true;
 }
 function updateSignals(){
  const active=new Set<string>();
  for(const signal of snapshot?.signals||[]){
   const p=signalPose(network,signal);if(detailRadius===0||Math.hypot(p.x-detailCenter.x,p.z-detailCenter.y)>detailRadius)continue;
   active.add(signal.id);let model=signalModels.get(signal.id);if(!model){model=createSignal();signalModels.set(signal.id,model);signalGroup.add(model);}
   model.position.set(p.x+Math.cos(p.angle)*3.2,0,p.z-Math.sin(p.angle)*3.2);model.rotation.y=p.angle;
   const lamp=model.getObjectByName('lamp') as THREE.Mesh<THREE.BufferGeometry,THREE.MeshStandardMaterial>;
   const color=signal.failed?0x48504c:signal.aspect==='green'?0x53bb83:signal.aspect==='yellow'?0xe9b84f:0xe3584e;
   lamp.material.color.setHex(color);lamp.material.emissive.setHex(color);lamp.material.emissiveIntensity=signal.failed?0:.6;
  }
  for(const [id,model] of signalModels)if(!active.has(id)){signalGroup.remove(model);disposeObject(model);signalModels.delete(id);}
  for(const [vertex,model] of switchModels){const locked=snapshot?.switches.some(s=>s.vertex===vertex&&s.locked);model.scale.setScalar(locked?1.2:1);}
 }
 function updateAuthority(){
  clearGroup(authority);
  for(const [key,color] of [['reserved_blocks',0x8864b1],['occupied_blocks',0xd19b42]] as const){
   const positions:number[]=[],seen=new Set<string>();
   for(const train of snapshot?.trains||[])for(const block of train[key]||[]){if(key==='reserved_blocks'&&block.occupied||seen.has(block.resource))continue;seen.add(block.resource);for(let i=1;i<block.geometry.length;i++){const a=projectLatLon(block.geometry[i-1]),b=projectLatLon(block.geometry[i]);positions.push(a[0],.6,a[1],b[0],.6,b[1]);}}
   if(positions.length){const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));authority.add(new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color,transparent:true,opacity:.7,depthWrite:false})));}
  }
 }
 function createView(train:EraTrain):TrainView{
  const model=createArticulatedTrain(train.spec.mass_t>=1000?'freight':'passenger',Math.max(0,Math.min(12,Math.round((train.spec.length_m-20)/23.4))));
  // Bogies are positioned in the consist frame, independently of stretched bodies.
  for(const vehicle of model.userData.vehicles)for(const bogie of vehicle.bogies){vehicle.object.remove(bogie.object);model.add(bogie.object);}
  model.userData.trainId=train.id;scene.add(model);
  const label=document.createElement('div');label.className='label train';$('labels').append(label);
  return {model,label,sampler:null,motion:new TrainMotionBuffer(),train,length:train.spec.length_m,head:new THREE.Vector3()};
 }
 function updateTrainLabel(view:TrainView){
  const train=view.train;
  view.label.replaceChildren(document.createTextNode(train.name||train.id));
  const sub=document.createElement('small');sub.textContent=`${train.id} · ${train.speed_kmh.toFixed(0)} ${t('km/h')} · ${t(train.state.replaceAll('_',' '))}`;view.label.append(sub);
 }
 function updateHeading(){
  $('scene-title').textContent=selected&&trainViews.has(selected)?trainViews.get(selected)!.train.name:t('Railway network');
  $('scene-status').textContent=snapshot?t('{mode} · {trains} trains · {sections} track sections',{mode:t(replay?'REPLAY':snapshot.running?'LIVE':'PAUSED'),trains:localizedNumber(trainViews.size),sections:localizedNumber(sectionCount)}):t('{stations} stations & stops · waiting for live state',{stations:localizedNumber(network.stations.length)});
 }
 function updateScale(){const distance=camera.position.distanceTo(controls.target);$('scale').textContent=t('{distance} {unit} view',{distance:distance>10000?(distance/1000).toFixed(0):distance.toFixed(0),unit:t(distance>10000?'km':'m')});}
 refreshLanguage=()=>{updateFollow();updateHeading();trainViews.forEach(updateTrainLabel);updateScale();dirty=true;};
 receive=data=>{
  // Selection can change without a new snapshot version.
  selected=data.selected;dirty=true;
  const state=data.state,now=performance.now();
  if(snapshot&&snapshot.run_id===state.run_id&&!data.replay&&!replay&&state.version<snapshot.version)return;
  const changed=!snapshot||snapshot.run_id!==state.run_id||snapshot.version!==state.version||replay!==data.replay;
  if(!changed){updateHeading();return;}
  if(snapshot&&snapshot.run_id!==state.run_id){initialFocus=false;acknowledged=-1;}
  const discontinuity=!snapshot||snapshot.run_id!==state.run_id||replay!==data.replay||data.replay;
  snapshot=state;replay=data.replay;
  const motionFrame=toMotionFrame(state);
  const motionTrains=new Map(motionFrame.trains.map(train=>[train.id,train]));
  const ids=new Set(state.trains.filter(t=>t.state!=='completed').map(t=>t.id));
  for(const [id,view] of trainViews)if(!ids.has(id)){scene.remove(view.model);disposeObject(view.model);view.label.remove();trainViews.delete(id);}
  for(const train of state.trains){
   if(!ids.has(train.id))continue;
   let view=trainViews.get(train.id);
   if(view&&view.length!==train.spec.length_m){scene.remove(view.model);disposeObject(view.model);view.label.remove();trainViews.delete(train.id);view=undefined;}
   if(!view){view=createView(train);trainViews.set(train.id,view);}
   if(discontinuity||!canInterpolateTrain(network,view.train,train))view.motion=new TrainMotionBuffer();
   const signature=routeSignature(train.route);
   if(signature!==view.sampler?.signature){try{view.sampler=createRouteSampler(network,train.route);}catch{view.sampler=null;}}
   view.train=train;
   view.motion.push({...motionFrame,trains:[motionTrains.get(train.id)!]},now);
   updateTrainLabel(view);
  }
  metrics.modelTrains=trainViews.size;
  updateHeading();
  updateSignals();updateAuthority();
  if(!initialFocus&&trainViews.size){focusTrain(activeId());initialFocus=true;}
 };
 function labelAt(element:HTMLElement,position:THREE.Vector3){
  vector.copy(position).project(camera);const visible=vector.z>=-1&&vector.z<=1&&Math.abs(vector.x)<1.1&&Math.abs(vector.y)<1.1;
  element.hidden=!visible;if(visible){element.style.left=`${(vector.x*.5+.5)*innerWidth}px`;element.style.top=`${(-vector.y*.5+.5)*innerHeight}px`;}
 }
 function drawTrain(view:TrainView,now:number){
  const {model,train,sampler}=view,headDistance=view.motion.sample(train.id,now)?.position??train.distance_m;
  const head=sampler?.(headDistance),point=head?[head.x,head.z]:projectLatLon(train.position);
  view.head.set(point[0],0,point[1]);model.position.set(point[0],.39,point[1]);
  const scale=view.length/model.userData.length;
  for(const vehicle of model.userData.vehicles){
   const center=headDistance-(model.userData.frontOffset+vehicle.centerOffset)*scale;
   const pose=sampler?vehiclePose(sampler,center,vehicle.wheelbase*scale):{x:point[0],z:point[1]-(model.userData.frontOffset+vehicle.centerOffset)*scale,angle:0};
   vehicle.object.position.set(pose.x-point[0],0,pose.z-point[1]);vehicle.object.rotation.y=pose.angle;vehicle.object.scale.z=scale;
   for(const bogie of vehicle.bogies){const at=center+bogie.offset*scale,bp=sampler?vehiclePose(sampler,at,1.66):{x:pose.x,z:pose.z+bogie.offset*scale,angle:0};bogie.object.position.set(bp.x-point[0],0,bp.z-point[1]);bogie.object.rotation.y=bp.angle;}
  }
  view.label.classList.toggle('selected',train.id===selected);
 }
 const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();let pointerDown=[0,0];
 renderer.domElement.addEventListener('pointerdown',event=>{pointerDown=[event.clientX,event.clientY];});
 renderer.domElement.addEventListener('pointerup',event=>{
  if(event.button!==0||Math.hypot(event.clientX-pointerDown[0],event.clientY-pointerDown[1])>5)return;
  pointer.set(event.clientX/innerWidth*2-1,-event.clientY/innerHeight*2+1);raycaster.setFromCamera(pointer,camera);
  const hit=raycaster.intersectObjects([...trainViews.values()].filter(v=>v.model.visible).map(v=>v.model),true)[0];
  if(hit){let object:THREE.Object3D|null=hit.object;while(object&&!object.userData.trainId)object=object.parent;if(object){selected=object.userData.trainId;post({type:'select-train',id:selected});dirty=true;}}
 });
 function animate(now:number){
  requestAnimationFrame(animate);
  const dt=Math.min(.1,(now-lastTime)/1000);lastTime=now;
  if(document.hidden)return;
  let animating=false;for(const view of trainViews.values()){animating=view.motion.isAnimating(now)||animating;drawTrain(view,now);}
  if(follow){const view=trainViews.get(activeId());if(view){const delta=view.head.clone().sub(controls.target).multiplyScalar(1-Math.exp(-dt/0.12));controls.target.add(delta);camera.position.add(delta);dirty=true;}}
  const controlsChanged=controls.update();
  updateDetail(now);
  if(!dirty&&!animating&&!controlsChanged)return;
  dirty=animating;
  const distance=camera.position.distanceTo(controls.target);camera.near=Math.max(.25,distance/5000);camera.far=Math.max(5000,distance*8+4000);camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  ground.position.x=controls.target.x;ground.position.z=controls.target.z;ground.scale.setScalar(Math.max(10000,distance*10));
  // Staged services may share one authoritative origin. A stable representative
  // avoids coplanar meshes flickering; every train stays selectable in the table.
  const visibleHeads:THREE.Vector3[]=[];
  const ordered=[...trainViews.values()].sort((a,b)=>Number(b.train.id===selected)-Number(a.train.id===selected)||a.train.id.localeCompare(b.train.id));
  for(const view of ordered){const duplicate=visibleHeads.some(head=>head.distanceToSquared(view.head)<.25);
   view.model.visible=distance<100000&&!duplicate;if(!duplicate)visibleHeads.push(view.head);
   view.label.dataset.coincident=String(duplicate);
  }
  stationDots.visible=distance>4500;stationBuildings.visible=distance<9000;
  for(const view of trainViews.values()){labelAt(view.label,view.head.clone().setY(9));if(view.label.dataset.coincident==='true')view.label.hidden=true;}
  stationLabels.forEach(label=>{labelAt(label.element,label.position);if(!layerState.stations)label.element.hidden=true;});
  renderer.render(scene,camera);
  metrics.frames=++frameCount;metrics.drawCalls=renderer.info.render.calls;metrics.triangles=renderer.info.render.triangles;
  if(snapshot&&snapshot.version!==acknowledged){acknowledged=snapshot.version;metrics.lastVersion=acknowledged;post({type:'map-rendered',version:acknowledged});}
  updateScale();
 }
 $('loading').hidden=true;
 updateHeading();
 if(pending)receive(pending);
 requestAnimationFrame(animate);post({type:'map-ready'});
}
function showError(message:string){activeError=message;$('loading').hidden=false;$('loading').replaceChildren();const title=document.createElement('strong');title.textContent=t('3D view unavailable');const text=document.createElement('span');text.textContent=t(message)+' '+t('The 2D map and simulation remain available.');$('loading').append(title,text);post({type:'scene3d-error',message});}
main().catch(error=>{console.error('Railflow 3D:',error);showError(error instanceof Error&&error.message==='Track geometry could not be loaded.'?error.message:'The 3D railway view could not be initialized.');});
