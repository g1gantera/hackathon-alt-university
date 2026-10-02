import {useSyncExternalStore} from 'react';
import {getLanguage,languages,setLanguage,subscribeLanguage,translate} from './i18n/core';

export const useLanguage=()=>useSyncExternalStore(subscribeLanguage,getLanguage,()=> 'ru' as const);
export function initializeLanguage(){
 const apply=()=>{
  document.documentElement.lang=getLanguage();
  document.title=(location.pathname.includes('stage3')?'RailFlow Lab':'RailFlow')+' • '+translate('Астана — Кокшетау');
 };
 apply();subscribeLanguage(apply);
 window.addEventListener('storage',event=>{
  if(event.key==='railflow-language'&&languages.includes(event.newValue as typeof languages[number]))setLanguage(event.newValue as typeof languages[number]);
 });
}
