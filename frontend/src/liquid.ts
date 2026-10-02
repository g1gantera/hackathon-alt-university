import {useLayoutEffect,type RefObject} from 'react';

type BrandedNavigator=Navigator&{userAgentData?:{brands:{brand:string}[]}};

/** Let the specular highlight of the glass under the pointer follow it; enable refraction where it renders. */
export function installLiquidGlass(){
 let frame=0,lit:HTMLElement|null=null,x=0,y=0;
 const paint=()=>{
  frame=0;
  if(!lit)return;
  const box=lit.getBoundingClientRect();
  lit.style.setProperty('--mx',`${x-box.left}px`);
  lit.style.setProperty('--my',`${y-box.top}px`);
 };
 addEventListener('pointermove',event=>{
  const glass=(event.target as Element|null)?.closest?.<HTMLElement>('.glass')??null;
  if(lit!==glass){lit?.classList.remove('lit');glass?.classList.add('lit');lit=glass;}
  x=event.clientX;y=event.clientY;
  if(lit&&!frame)frame=requestAnimationFrame(paint);
 },{passive:true});
 document.documentElement.addEventListener('pointerleave',()=>{lit?.classList.remove('lit');lit=null;});
 // Refraction is an SVG filter inside backdrop-filter, which only Chromium renders.
 const brands=(navigator as BrandedNavigator).userAgentData?.brands??[];
 if(brands.some(b=>b.brand==='Chromium')&&CSS.supports('backdrop-filter','url(#rf-refraction) blur(2px)'))document.documentElement.classList.add('rf-refract');
}

/** Slide a glass lens under the `.on` child of a segmented control. */
export function useLens(ref:RefObject<HTMLElement|null>,active:unknown){
 useLayoutEffect(()=>{
  const host=ref.current;
  if(!host)return;
  const place=()=>{
   const on=host.querySelector<HTMLElement>(':scope > .on');
   host.style.setProperty('--lens-x',`${on?.offsetLeft??0}px`);
   host.style.setProperty('--lens-w',`${on?.offsetWidth??0}px`);
   host.classList.toggle('has-lens',!!on);
  };
  place();
  // Animate only after the first placement so the lens does not slide in from the edge.
  const ready=requestAnimationFrame(()=>host.classList.add('lens-ready'));
  const observer=new ResizeObserver(place);
  observer.observe(host);
  return()=>{cancelAnimationFrame(ready);observer.disconnect();};
 },[ref,active]);
}
