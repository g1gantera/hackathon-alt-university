import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source=readFileSync(new URL('../../static/scene3d-toggle.js',import.meta.url),'utf8');

function harness(){
 class Element {
  constructor(){
   this.children=[];this.attributes=new Map();this.listeners=new Map();this.hidden=false;this.className='';
   const classes=new Set();
   this.classList={toggle:(name,on)=>on?classes.add(name):classes.delete(name),contains:name=>classes.has(name)};
  }
  setAttribute(name,value){this.attributes.set(name,String(value));}
  getAttribute(name){return this.attributes.get(name)??null;}
  addEventListener(name,callback){this.listeners.set(name,callback);}
  appendChild(child){this.children.push(child);}
  prepend(child){this.children.unshift(child);}
  after(child){this.nextSibling=child;}
  click(){this.listeners.get('click')?.();}
 }
 const frame=new Element(),loading=new Element(),tools=new Element(),wrapper=new Element();
 frame.title='Original railway map with live simulation overlays';frame.contentWindow={};
 const navigations=[];let url='about:blank';
 Object.defineProperty(frame,'src',{get:()=>url,set:value=>{url=value;navigations.push(value);}});
 frame.closest=selector=>selector==='.map-wrapper'?wrapper:null;
 const nodes=new Map([['rail-map',frame],['map-loading',loading]]);
 const messages=[];
 const document={getElementById:id=>nodes.get(id),querySelector:selector=>selector==='.map-tools'?tools:null,createElement:()=>new Element()};
 const window={addEventListener:(name,callback)=>{if(name==='message')messages.push(callback);}};
 const location={origin:'http://127.0.0.1:8001'};
 const forbidden=()=>assert.fail('The view toggle must not call an API or modify the dispatcher');
 runInNewContext(source,{document,window,location,fetch:forbidden,action:forbidden,api:forbidden});
 return {frame,loading,tools,wrapper,navigations,
  buttons:tools.children[0].children,
  message:event=>messages.forEach(callback=>callback(event)),
  origin:location.origin};
}

test('2D remains the default without loading a viewer or touching dispatcher actions',()=>{
 const h=harness();
 assert.equal(h.frame.src,'about:blank');
 assert.equal(h.navigations.length,0);
 assert.equal(h.wrapper.classList.contains('is-3d'),false);
 assert.equal(h.wrapper.nextSibling.hidden,true);
 assert.equal(h.tools.children[0].getAttribute('role'),'group');
 assert.equal(h.tools.children[0].getAttribute('aria-label'),'Map view');
 assert.deepEqual(h.buttons.map(button=>button.getAttribute('aria-pressed')),['true','false']);
 h.buttons[0].click();
 assert.equal(h.navigations.length,0);
});

test('view switches retain the original iframe and restore the 2D title and loading message',()=>{
 const h=harness(),frame=h.frame,window=frame.contentWindow,title=frame.title;
 h.buttons[1].click();
 assert.equal(h.frame,frame);assert.equal(h.frame.contentWindow,window);
 assert.equal(frame.src,'/static/scene3d/index.html');
 assert.equal(h.wrapper.classList.contains('is-3d'),true);
 assert.equal(h.wrapper.nextSibling.hidden,false);
 assert.equal(h.loading.textContent,'Loading 3D railway view…');
 assert.equal(h.loading.hidden,false);
 assert.equal(frame.getAttribute('aria-busy'),'true');
 assert.deepEqual(h.buttons.map(button=>button.getAttribute('aria-pressed')),['false','true']);
 h.buttons[1].click();
 assert.equal(h.navigations.length,1,'Repeated 3D selection must not reload the renderer');
 h.buttons[0].click();
 assert.equal(frame.src,'/map-live');
 assert.equal(frame.title,title);
 assert.equal(h.wrapper.classList.contains('is-3d'),false);
 assert.equal(h.wrapper.nextSibling.hidden,true);
 assert.equal(h.loading.textContent,'Loading original railway map…');
 assert.deepEqual(h.navigations,['/static/scene3d/index.html','/map-live']);
});

test('only the same-origin current map iframe can acknowledge view readiness',()=>{
 const h=harness();h.buttons[1].click();
 const ready={origin:h.origin,source:h.frame.contentWindow,data:{type:'map-ready'}};
 h.message({...ready,origin:'https://unrelated.example'});
 assert.equal(h.loading.hidden,false);
 h.message({...ready,source:{}});
 assert.equal(h.loading.hidden,false);
 h.message({...ready,data:{type:'state'}});
 assert.equal(h.loading.hidden,false);
 h.message({...ready,data:null});
 assert.equal(h.loading.hidden,false);
 h.message(ready);
 assert.equal(h.loading.hidden,true);
 assert.equal(h.frame.getAttribute('aria-busy'),'false');
});

test('the optional enhancement is harmless when no map panel exists',()=>{
 runInNewContext(source,{document:{getElementById:()=>null,querySelector:()=>null}});
});

test('a 3D renderer error reveals the iframe diagnostic and retains the 2D fallback',()=>{
 const h=harness();h.buttons[1].click();
 const error={origin:h.origin,source:h.frame.contentWindow,data:{type:'scene3d-error'}};
 h.message(error);
 assert.equal(h.loading.hidden,true);
 assert.equal(h.frame.getAttribute('aria-busy'),'false');
 assert.equal(h.frame.src,'/static/scene3d/index.html');
 h.buttons[0].click();
 h.message(error);
 assert.equal(h.frame.src,'/map-live');
 assert.equal(h.loading.textContent,'Loading original railway map…');
});
