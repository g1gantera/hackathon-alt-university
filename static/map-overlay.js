/* Separate overlay adapter. Does not mutate any original geometry, style or topology. */
(() => {
  const t=(source,params)=>window.RailI18n?.t(source,params)??source;
  const message=source=>window.RailI18n?.message(source)??source;
  try { if(parent!==window&&parent.RailI18n)window.RailI18n?.setLanguage(parent.RailI18n.language,false); } catch { /* Only a same-origin parent shares preferences. */ }
  const menu=document.getElementById('panel');
  menu.style.display='none';
  const layer=L.layerGroup().addTo(map);
  const routeLayer=L.layerGroup().addTo(map);
  const markers=new Map();
  let latest=null,selected=null,first=true,signature='',version=-1,run=null;
  const style=document.createElement('style');
  style.textContent='.train-overlay{background:transparent;border:0}.train-body{background:#2563eb;color:white;border:2px solid white;border-radius:6px;box-shadow:0 2px 6px #0004;padding:3px 6px;white-space:nowrap;font:600 10px system-ui;text-align:center}.train-body.selected{background:#175238;box-shadow:0 0 0 3px #17523844}.signal-overlay{border:2px solid white;border-radius:50%;box-shadow:0 0 0 1px #23392e77}.signal-red{background:#ce4141}.signal-green{background:#40a060}.signal-yellow{background:#e6aa31}.incident-overlay{background:#c74848;color:white;border:2px solid white;border-radius:50%;font:bold 11px system-ui;text-align:center;box-shadow:0 1px 5px #0003}';
  document.head.appendChild(style);
  function icon(t){return L.divIcon({className:'train-overlay',html:`<div class="train-body ${t.id===selected?'selected':''}">▣ ${esc(t.id)}</div>`,iconSize:[76,23],iconAnchor:[38,11]});}
  function render(s){
    latest=s;
    const ids=new Set(s.trains.map(t=>t.id));
    for(const [id,m] of markers){if(!ids.has(id)){map.removeLayer(m);markers.delete(id);}}
    s.trains.forEach(t=>{
      let m=markers.get(t.id);
      if(!m){m=L.marker(t.position,{icon:icon(t),zIndexOffset:1000}).addTo(map);m.on('click',()=>parent.postMessage({type:'select-train',id:t.id},location.origin));markers.set(t.id,m);}
      m.setLatLng(t.position).setIcon(icon(t));
      m.bindTooltip(`${esc(t.name)} · ${t.speed_kmh.toFixed(1)} ${esc(message('km/h'))}<br>${esc(message(t.state.replaceAll('_',' ')))} · ${esc(message('importance'))} ${t.importance}<br>${esc(message(t.reason))}`);
      m.setOpacity(t.state==='completed'?.4:1);
    });
    const sig=JSON.stringify([selected,s.trains.map(t=>[t.id,t.reserved_blocks?.map(b=>b.resource)??t.reserved_edges,t.occupied_blocks?.map(b=>b.resource)??t.occupied_edges]),s.signals.map(x=>[x.id,x.aspect,x.failed,x.owner,x.occupied_by]),s.incidents.map(x=>[x.id,x.status]),s.switches]);
    if(sig!==signature){
      signature=sig; layer.clearLayers();routeLayer.clearLayers();
      for(const t of s.trains){
        const reserved=t.reserved_blocks??t.reserved_edges.map(eid=>({geometry:E[eid]?.pts}));
        const occupied=t.occupied_blocks??t.occupied_edges.map(eid=>({geometry:E[eid]?.pts}));
        for(const b of reserved){if(b.geometry&&!b.occupied)L.polyline(b.geometry,{weight:t.id===selected?8:6,color:'#8b5cf6',opacity:.35,interactive:false}).addTo(routeLayer);}
        for(const b of occupied){if(b.geometry)L.polyline(b.geometry,{weight:6,color:'#ef9b35',opacity:.65,interactive:false}).addTo(layer);}
      }
      for(const sgn of s.signals){
        L.marker(sgn.position,{icon:L.divIcon({className:`signal-overlay signal-${sgn.aspect}`,iconSize:[9,9],iconAnchor:[4,4]}),zIndexOffset:500}).bindTooltip(`${esc(sgn.id)} · ${esc(t(sgn.aspect))}<br>${esc(message(sgn.reason))}<br>${esc(t('Reserved for:'))} ${esc(sgn.owner||t('none'))}<br>${esc(t('Occupied by:'))} ${esc(sgn.occupied_by||t('none'))}`).addTo(layer);
      }
      for(const sw of s.switches){
        if(V[sw.vertex])L.circleMarker(V[sw.vertex],{radius:5,weight:2,color:'#8867c1',fillColor:'#fff',fillOpacity:1}).bindTooltip(`V${sw.vertex} ${esc(t('locked'))} · ${esc(sw.owner)}<br>${esc(t('Route'))} ${sw.position.join(' → ')}`).addTo(layer);
      }
      for(const inc of s.incidents.filter(i=>i.status==='active')){
        let pos;
        if(inc.asset_type==='train')pos=s.trains.find(t=>t.id===inc.asset_id)?.position;
        else if(inc.asset_type==='station'||inc.asset_type==='switch')pos=V[+inc.asset_id];
        else{const eid=inc.asset_type==='signal'?+inc.asset_id.split('-')[1]:+inc.asset_id;const e=E[eid];if(e){pos=e.pts[Math.floor(e.pts.length/2)];L.polyline(e.pts,{weight:7,color:'#ce4141',opacity:.6,dashArray:'8 8',interactive:false}).addTo(layer);}}
        if(pos)L.marker(pos,{icon:L.divIcon({className:'incident-overlay',html:'!',iconSize:[18,18],iconAnchor:[9,9]})}).bindTooltip(`${esc(message(inc.kind.replaceAll('_',' ')))} · ${esc(inc.id)}`).addTo(layer);
      }
    }
    if(first&&s.trains.length){focus();first=false;}
    parent.postMessage({type:'map-rendered',version:s.version},location.origin);
  }
  function focus(){
    if(!latest?.trains.length)return;
    const pts=[];
    latest.trains.forEach(t=>t.route_edges.forEach(eid=>{if(E[eid])pts.push(...E[eid].pts);}));
    if(pts.length)map.fitBounds(L.latLngBounds(pts),{padding:[45,45],maxZoom:15});
  }
  // Bind only UI text in the preserved original map. Keep names, option values,
  // graph geometry and routing code untouched; remember English for switching back.
  const textSources=new WeakMap(),attributeSources=new WeakMap();
  const uiRoots=[menu,document.getElementById('info'),...document.querySelectorAll('.leaflet-tooltip-pane,.leaflet-control')].filter(Boolean);
  function mapText(source){
    let match=source.match(/^(.+) · ([\d.,]+) km segment$/);
    if(match)return t('{category} · {distance} km segment',{category:t(match[1]),distance:match[2]});
    match=source.match(/^(.* · )(station|halt|tram_stop|tram stop)$/);
    if(match)return match[1]+t(match[2]);
    match=source.match(/^([\d.,\s]+) (km|m)$/);
    if(match)return `${match[1]} ${t(match[2])}`;
    return message(source);
  }
  function translateOriginalMap(){
    observer.disconnect();
    document.title=t('Rail Network Map');
    for(const root of uiRoots){
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()){
        const node=walker.currentNode;
        if(node.parentElement?.closest('script,style,#rFrom option:not([value=""]),#rTo option:not([value=""])'))continue;
        const previous=textSources.get(node),current=node.nodeValue;
        const source=previous&&current===previous.rendered?previous.source:current;
        const rendered=source.replace(/\S(?:[\s\S]*\S)?/,value=>mapText(value));
        if(current!==rendered)node.nodeValue=rendered;
        textSources.set(node,{source,rendered});
      }
      for(const node of root.querySelectorAll('[placeholder],[title],[aria-label]')){
        const saved=attributeSources.get(node)||{};
        for(const attribute of ['placeholder','title','aria-label']){
          if(!node.hasAttribute(attribute))continue;
          const current=node.getAttribute(attribute),previous=saved[attribute];
          const source=previous&&current===previous.rendered?previous.source:current,rendered=t(source);
          if(current!==rendered)node.setAttribute(attribute,rendered);
          saved[attribute]={source,rendered};
        }
        attributeSources.set(node,saved);
      }
    }
    for(const root of uiRoots)observer.observe(root,{childList:true,subtree:true,characterData:true});
  }
  const observer=new MutationObserver(translateOriginalMap);
  translateOriginalMap();
  window.addEventListener('railflow:languagechange',()=>{
    signature='';
    if(latest)render(latest);
    translateOriginalMap();
  });
  window.addEventListener('message',ev=>{
    if(ev.origin!==location.origin||ev.source!==parent)return;
    const d=ev.data;
    if(d.type==='state'){
      if(!d.replay&&d.state.run_id===run&&d.state.version<=version)return;
      if(d.state.run_id!==run)first=true;
      run=d.state.run_id;version=d.state.version;selected=d.selected;
      render(d.state);
    } else if(d.type==='focus')focus();
    else if(d.type==='layers')menu.style.display=menu.style.display==='none'?'block':'none';
  });
  parent.postMessage({type:'map-ready'},location.origin);
})();
