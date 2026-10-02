import {create} from 'zustand';
import {flushSync} from 'react-dom';

export type ThemeMode='system'|'light'|'dark';
export type Theme='light'|'dark';

// index.html applies the same key before the first paint to avoid a light flash.
const key='railflow-theme';
const media=typeof matchMedia==='function'?matchMedia('(prefers-color-scheme: dark)'):null;

function storedMode():ThemeMode{
 try{const value=localStorage.getItem(key);return value==='light'||value==='dark'?value:'system';}
 catch{return 'system';}
}
const resolve=(mode:ThemeMode):Theme=>mode==='system'?(media?.matches?'dark':'light'):mode;

function apply(theme:Theme){
 const root=document.documentElement;
 root.dataset.theme=theme;
 root.style.colorScheme=theme;
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#0e1417':'#e7eeec');
}

const initial=storedMode();
apply(resolve(initial));

export const useTheme=create<{mode:ThemeMode;theme:Theme}>(()=>({mode:initial,theme:resolve(initial)}));

/** Switch theme; when supported, reveal the new one as a circle growing from the pointer. */
export function setThemeMode(mode:ThemeMode,origin?:{x:number;y:number}){
 try{if(mode==='system')localStorage.removeItem(key);else localStorage.setItem(key,mode);}catch{/* storage may be blocked */}
 const theme=resolve(mode);
 const commit=()=>flushSync(()=>{apply(theme);useTheme.setState({mode,theme});});
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 if(theme===useTheme.getState().theme||reduced||!document.startViewTransition){commit();return;}
 const x=origin?.x??innerWidth-80,y=origin?.y??40;
 const radius=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
 document.startViewTransition(commit).ready.then(()=>{
  document.documentElement.animate({clipPath:[`circle(0px at ${x}px ${y}px)`,`circle(${radius}px at ${x}px ${y}px)`]},{duration:520,easing:'cubic-bezier(.3,.8,.3,1)',pseudoElement:'::view-transition-new(root)'});
 }).catch(()=>{});
}

media?.addEventListener('change',()=>{
 if(useTheme.getState().mode!=='system')return;
 const theme=resolve('system');
 apply(theme);
 useTheme.setState({theme});
});
