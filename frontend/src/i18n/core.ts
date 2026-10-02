import {catalog} from './catalog.ts';

export type Language='kk'|'ru'|'en';
export const languages:Language[]=['kk','ru','en'];
const storageKey='railflow-language';
export function storedLanguage(storage?:Pick<Storage,'getItem'>):Language{
 try{const value=storage?.getItem(storageKey);return languages.includes(value as Language)?value as Language:'ru';}catch{return 'ru';}
}
let language:Language=storedLanguage(typeof localStorage==='undefined'?undefined:localStorage);
const listeners=new Set<()=>void>(),cache=new Map<string,string>();
export const getLanguage=()=>language;
export const intlLocale=()=>({kk:'kk-KZ',ru:'ru-RU',en:'en-GB'}[language]);
export function subscribeLanguage(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener);};}
export function setLanguage(value:Language){
 if(!languages.includes(value))return;
 try{if(typeof localStorage!=='undefined')localStorage.setItem(storageKey,value);}catch{/* private storage */}
 if(language===value)return;
 language=value;cache.clear();for(const listener of listeners)listener();
}
const aliases=new Map<string,string>();
const templates:{key:string;pattern:RegExp;parameters:number[]}[]=[];
for(const [key,translations] of Object.entries(catalog)){
 for(const text of new Set([key,...translations.filter((x):x is string=>!!x)])){
  aliases.set(text,key);
  if(!/\{\d+\}/.test(text))continue;
  const parameters:number[]=[];
  const escaped=text.replace(/[.*+?^$()|[\]\\]/g,'\\$&').replace(/\{(\d+)\}/g,(_,index)=>{parameters.push(Number(index));return '(.+?)';});
  templates.push({key,parameters,pattern:new RegExp('^'+escaped+'$','u')});
 }
}
const names=['Астана','Кокшетау','Шортанды','Акколь','Макинск','Бурабай'];
function translated(key:string){const entry=catalog[key];return language==='ru'?entry?.[2]??key:entry?.[language==='en'?0:1]??key;}
export function translate(source:string,...parameters:unknown[]):string{
 const key=source.trim(),prefix=source.slice(0,source.length-source.trimStart().length),suffix=source.slice(source.trimEnd().length);
 return prefix+translated(key).replace(/\{(\d+)\}/g,(_,i)=>String(displayText(parameters[Number(i)]??`{${i}}`)))+suffix;
}
/** Translate human text only at presentation boundaries. Never mutate IDs, snapshots or form values. */
export function displayText<T>(value:T):T{
 if(typeof value!=='string')return value;
 const text=value.trim();if(!text)return value;
 const cached=cache.get(value);if(cached!==undefined)return cached as T;
 let result:string|undefined;
 const key=Object.hasOwn(catalog,text)?text:aliases.get(text);
 if(key)result=translated(key);
 else for(const template of templates){
  const match=template.pattern.exec(text);if(!match)continue;
  const args:unknown[]=[];template.parameters.forEach((index,i)=>{args[index]=match[i+1];});
  result=translate(template.key,...args);break;
 }
 if(result===undefined){
  result=text;
  for(const name of names){
   const entry=catalog[name];if(!entry)continue;
   for(const alias of new Set([name,...entry.filter((s):s is string=>!!s)]))result=result.replaceAll(alias,translated(name));
  }
 }
 const output=value.slice(0,value.length-value.trimStart().length)+result+value.slice(value.trimEnd().length);
 if(cache.size>=2000)cache.clear();cache.set(value,output);return output as T;
}
