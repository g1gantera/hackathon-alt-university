/* Presentation-only view switch. The existing dashboard owns all state/actions. */
(() => {
  'use strict';
  const frame=document.getElementById('rail-map');
  const loading=document.getElementById('map-loading');
  const tools=document.querySelector('.map-tools');
  const wrapper=frame?.closest('.map-wrapper');
  if(!frame||!loading||!tools||!wrapper)return;

  const originalTitle=frame.title;
  let mode='2d';
  const group=document.createElement('div');
  group.className='scene3d-view-switch';
  group.setAttribute('role','group');
  group.setAttribute('aria-label','Map view');
  const buttons=new Map();
  for(const [value,label,title] of [
    ['2d','2D','Geographic railway map'],
    ['3d','3D','Low-poly railway view'],
  ]){
    const button=document.createElement('button');
    button.type='button';button.textContent=label;button.title=title;
    button.setAttribute('aria-label',title);
    button.setAttribute('aria-controls','rail-map');
    button.setAttribute('aria-pressed',String(value===mode));
    button.addEventListener('click',()=>show(value));
    group.appendChild(button);buttons.set(value,button);
  }
  tools.prepend(group);
  const hint=document.createElement('p');
  hint.className='scene3d-view-hint';hint.hidden=true;
  hint.textContent='3D models show the same trains, routes and signals as the selected live or replay view.';
  wrapper.after(hint);
  frame.setAttribute('allowfullscreen','');

  function show(next){
    if(next===mode)return;
    mode=next;
    const is3D=next==='3d';
    wrapper.classList.toggle('is-3d',is3D);
    hint.hidden=!is3D;
    for(const [value,button] of buttons)button.setAttribute('aria-pressed',String(value===mode));
    frame.title=is3D?'3D railway view with live simulation overlays':originalTitle;
    frame.setAttribute('aria-busy','true');
    loading.textContent=is3D?'Loading 3D railway view…':'Loading original railway map…';
    loading.setAttribute('role','status');loading.hidden=false;
    // Keep the iframe identity: app.js validates messages against its window
    // and sends the currently selected live/replay snapshot on map-ready.
    frame.src=is3D?'/static/scene3d/index.html':'/map-live';
  }

  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==frame.contentWindow)return;
    if(mode==='3d'&&event.data?.type==='scene3d-error'){
      frame.setAttribute('aria-busy','false');
      // The iframe owns the diagnostic; leave it visible and keep 2D available.
      loading.hidden=true;
      return;
    }
    if(event.data?.type!=='map-ready')return;
    frame.setAttribute('aria-busy','false');
    loading.hidden=true;
  });
})();
