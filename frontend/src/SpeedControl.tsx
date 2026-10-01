import {useEffect,useState} from 'react';
import {Button,InputNumber} from 'antd';

export function SpeedControl({value,disabled=false,onApply}:{value:number;disabled?:boolean;onApply:(speed:number)=>void|Promise<void>}){
 const [draft,setDraft]=useState<number|null>(value),[saving,setSaving]=useState(false);
 useEffect(()=>setDraft(value),[value]);
 const valid=draft!==null&&Number.isSafeInteger(draft)&&draft>0;
 const apply=async()=>{if(!valid||disabled||saving)return;setSaving(true);try{await onApply(draft!);}finally{setSaving(false);}};
 return <span style={{display:'inline-flex',gap:6,alignItems:'center',flexWrap:'wrap'}}>
  <InputNumber aria-label="Множитель скорости" value={draft} min={1} max={Number.MAX_SAFE_INTEGER} step={1} controls={false} disabled={disabled||saving} onChange={setDraft} onPressEnter={()=>apply()} addonAfter="×" style={{width:155}}/>
  <Button size="small" disabled={disabled||saving||!valid||draft===value} onClick={apply}>Установить</Button>
  <small style={{color:'#6a8178',fontSize:10}}>Сейчас: {value}×</small>
 </span>;
}
