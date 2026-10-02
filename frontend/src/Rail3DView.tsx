import {useEffect,useRef,useState} from 'react';
import {useDispatch} from './store';
import {useTheme} from './theme';
import {createRail3DScene,type CameraMode,type Rail3DScene} from './rail3d/scene';
import type {Snapshot,Topology} from './types';

/** Live 3D view of the corridor: the same simulated trains as the map, drawn as rolling stock. */
export function Rail3DView({topology,snapshot,camera}:{topology:Topology;snapshot:Snapshot;camera:CameraMode}){
 const canvas=useRef<HTMLCanvasElement>(null),overlay=useRef<HTMLDivElement>(null),scene=useRef<Rail3DScene|null>(null);
 const {selected,select}=useDispatch();
 const theme=useTheme(s=>s.theme);
 const [error,setError]=useState('');
 const latest=useRef({camera,theme});latest.current={camera,theme};
 useEffect(()=>{
  if(!canvas.current||!overlay.current)return;
  try{
   scene.current=createRail3DScene(canvas.current,overlay.current,topology,select);
   scene.current.setCamera(latest.current.camera);scene.current.setTheme(latest.current.theme);setError('');
  }
  catch(e){setError((e as Error).message);}
  return()=>{scene.current?.dispose();scene.current=null;};
 },[topology,select]);
 useEffect(()=>{scene.current?.update(snapshot,selected);},[snapshot,selected]);
 useEffect(()=>{scene.current?.setCamera(camera);},[camera]);
 useEffect(()=>{scene.current?.setTheme(theme);},[theme]);
 return <div className="r3d">
  <canvas ref={canvas} aria-label="Трёхмерный вид участка с поездами"/>
  <div ref={overlay} className="r3d-overlay"/>
  {error&&<p className="r3d-error" role="alert">{error}</p>}
 </div>;
}
