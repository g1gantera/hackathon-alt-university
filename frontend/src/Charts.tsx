import {useEffect,useRef} from 'react';
import * as echarts from 'echarts';
import type {Plan,Profile,Snapshot,Topology,SpeedAdvice,QualityPoint} from './types';
import {clock,useDispatch} from './store';

function Chart({option,onSelect,height=260}:{option:echarts.EChartsOption;onSelect?:(id:string)=>void;height?:number}){
 const ref=useRef<HTMLDivElement>(null),chart=useRef<echarts.ECharts|null>(null);
 useEffect(()=>{if(!ref.current)return;const c=echarts.init(ref.current);chart.current=c;const observer=new ResizeObserver(()=>c.resize());observer.observe(ref.current);return()=>{observer.disconnect();c.dispose();};},[]);
 useEffect(()=>{chart.current?.setOption(option,true);chart.current?.off('click');chart.current?.on('click',p=>{if(p.seriesName)onSelect?.(p.seriesName.split('|')[0]);});},[option,onSelect]);
 return <div ref={ref} style={{height,width:'100%'}}/>;
}

export function TrainChart({topology,snapshot,baseline,preview}:{topology:Topology;snapshot:Snapshot;baseline:Plan|null;preview:Plan|null}){
 const {selected,select}=useDispatch();
 const active=preview||snapshot.plan;
 const series:echarts.LineSeriesOption[]=[];
 for(const train of snapshot.trains){
  for(const [plan,dashed] of [[baseline,true],[active,false]] as const){
   if(!plan)continue;
   const moves=plan.movements.filter(m=>m.train_id===train.id).sort((a,b)=>a.leg-b.leg);
   const route=train.direction>0?[0,1,2,3,4,5]:[5,4,3,2,1,0];
   const data=moves.flatMap(m=>[[m.start_s,route[m.leg]],[m.end_s,route[m.leg+1]]]);
   series.push({name:`${train.id}|${dashed?'Исходный':'План'}`,type:'line',showSymbol:false,data,lineStyle:{type:dashed?'dashed':'solid',width:train.id===selected?3:1.5,opacity:dashed?.25:train.id===selected?1:.55,color:train.type==='passenger'?'#147f71':'#bc8e42'},emphasis:{focus:'series'},markLine:!dashed&&train.id===selected?{silent:true,symbol:'none',lineStyle:{color:'#9da9ae',type:'dotted'},label:{formatter:'Сейчас'},data:[{xAxis:snapshot.sim_time_s}]}:undefined});
  }
 }
 return <Chart onSelect={select} option={{animation:false,grid:{top:25,right:35,bottom:50,left:135},tooltip:{trigger:'item',formatter:(p:unknown)=>{const item=p as {seriesName:string;value:number[]};return `${item.seriesName}<br/>${clock(item.value[0])}`;}},xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),color:'#8b969d'},splitLine:{lineStyle:{color:'#eef1f3'}}},yAxis:{type:'value',min:0,max:5,interval:1,axisLabel:{formatter:(v:number)=>topology.stations[v]?.name||'',color:'#617078'},splitLine:{lineStyle:{color:'#edf1f2'}}},dataZoom:[{type:'inside',xAxisIndex:0},{type:'slider',height:13,bottom:5,borderColor:'transparent',fillerColor:'#d8e9e3',handleSize:0}],series}}/>;
}

export function SpeedChart({profile,proposal=null,advice}:{profile:Profile|null;proposal?:Profile|null;advice?:SpeedAdvice}){
 if(!profile)return <div className="empty small">Профиль обновляется…</div>;
 if(!profile.points.length)return <div className="empty small">Профиль будет доступен после применения нового плана.</div>;
 const series:echarts.LineSeriesOption[]=[
  {name:'Ограничение',type:'line',showSymbol:false,data:profile.points.map(p=>[p[0],p[3]*3.6]),lineStyle:{color:'#c29b5f',type:'dashed',width:1.5}},
  {name:'Активный профиль',type:'line',showSymbol:false,data:profile.points.map(p=>[p[0],p[2]*3.6]),lineStyle:{color:'#168875',width:2},areaStyle:{color:'#168875',opacity:.06},markLine:advice?{silent:true,symbol:'none',lineStyle:{color:'#627b85',type:'dotted'},label:{formatter:'Сейчас'},data:[{xAxis:advice.sim_time_s}]}:undefined,
   markPoint:advice?{symbol:'circle',symbolSize:8,label:{show:false},data:[{name:'Текущая скорость',coord:[advice.sim_time_s,advice.current_speed_mps*3.6]}]}:undefined},
 ];
 if(proposal)series.push({name:'Экономичный вариант',type:'line',showSymbol:false,data:proposal.points.map(p=>[p[0],p[2]*3.6]),lineStyle:{color:'#6964b9',width:2,type:'dashed'}});
 return <Chart height={245} option={{animation:false,legend:{top:4,textStyle:{fontSize:10}},grid:{left:45,right:20,top:48,bottom:50},tooltip:{trigger:'axis',valueFormatter:v=>`${Number(v).toFixed(1)} км/ч`},xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),fontSize:10},splitLine:{show:false}},yAxis:{type:'value',name:'км/ч',axisLabel:{fontSize:10},splitLine:{lineStyle:{color:'#eef1f3'}}},dataZoom:[{type:'inside',xAxisIndex:0},{type:'slider',height:12,bottom:5}],series}}/>;
}

export function QualityChart({points}:{points:QualityPoint[]}) {
 if(!points.length)return <div className="empty small">Снимки качества ещё не сохранены.</div>;
 return <Chart height={210} option={{animation:false,grid:{left:42,right:22,top:20,bottom:34},
  tooltip:{trigger:'axis',valueFormatter:v=>Number(v).toFixed(1)+' / 100'},
  xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),fontSize:10},splitLine:{show:false}},
  yAxis:{type:'value',min:0,max:100,axisLabel:{fontSize:10},splitLine:{lineStyle:{color:'#eef1f3'}}},
  series:[{name:'Текущий индекс',type:'line',showSymbol:true,symbolSize:5,connectNulls:false,
   data:points.map(p=>[p.sim_time_s,p.index]),lineStyle:{color:'#168875',width:2},itemStyle:{color:'#168875'}}]}}/>;
}
