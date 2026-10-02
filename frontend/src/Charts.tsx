import {useEffect,useRef} from 'react';
import * as echarts from 'echarts';
import type {Plan,Profile,Snapshot,Topology,SpeedAdvice,QualityPoint} from './types';
import {clock,useDispatch} from './store';
import {useTheme} from './theme';

const palettes={
 light:{axis:'#8b969d',label:'#617078',grid:'#eef1f3',now:'#9da9ae',passenger:'#147f71',freight:'#bc8e42',active:'#168875',limit:'#c29b5f',eco:'#6964b9',marker:'#627b85',zoom:'#d8e9e3'},
 dark:{axis:'#8fa1a6',label:'#b9c8cc',grid:'rgba(255,255,255,.07)',now:'#7f9196',passenger:'#3cc4ab',freight:'#e2ad57',active:'#3cc4ab',limit:'#d8b57c',eco:'#a29df2',marker:'#a9babe',zoom:'rgba(60,196,171,.28)'},
};
const useInk=()=>palettes[useTheme(s=>s.theme)];

function Chart({option,onSelect,height=260}:{option:echarts.EChartsOption;onSelect?:(id:string)=>void;height?:number}){
 const ref=useRef<HTMLDivElement>(null),chart=useRef<echarts.ECharts|null>(null);
 const dark=useTheme(s=>s.theme)==='dark';
 // The built-in theme styles legends, tooltips and zoom sliders; the page shows through the chart.
 useEffect(()=>{if(!ref.current)return;const c=echarts.init(ref.current,dark?'dark':undefined);chart.current=c;const observer=new ResizeObserver(()=>c.resize());observer.observe(ref.current);return()=>{observer.disconnect();c.dispose();};},[dark]);
 useEffect(()=>{chart.current?.setOption({backgroundColor:'transparent',...option},true);chart.current?.off('click');chart.current?.on('click',p=>{if(p.seriesName)onSelect?.(p.seriesName.split('|')[0]);});},[option,onSelect,dark]);
 return <div ref={ref} style={{height,width:'100%'}}/>;
}

export function TrainChart({topology,snapshot,baseline,preview}:{topology:Topology;snapshot:Snapshot;baseline:Plan|null;preview:Plan|null}){
 const {selected,select}=useDispatch();
 const ink=useInk();
 const active=preview||snapshot.plan;
 const series:echarts.LineSeriesOption[]=[];
 for(const train of snapshot.trains){
  for(const [plan,dashed] of [[baseline,true],[active,false]] as const){
   if(!plan)continue;
   const moves=plan.movements.filter(m=>m.train_id===train.id).sort((a,b)=>a.leg-b.leg);
   const route=train.direction>0?[0,1,2,3,4,5]:[5,4,3,2,1,0];
   const data=moves.flatMap(m=>[[m.start_s,route[m.leg]],[m.end_s,route[m.leg+1]]]);
   series.push({name:`${train.id}|${dashed?'Исходный':'План'}`,type:'line',showSymbol:false,data,lineStyle:{type:dashed?'dashed':'solid',width:train.id===selected?3:1.5,opacity:dashed?.25:train.id===selected?1:.55,color:train.type==='passenger'?ink.passenger:ink.freight},emphasis:{focus:'series'},markLine:!dashed&&train.id===selected?{silent:true,symbol:'none',lineStyle:{color:ink.now,type:'dotted'},label:{formatter:'Сейчас',color:ink.label},data:[{xAxis:snapshot.sim_time_s}]}:undefined});
  }
 }
 return <Chart onSelect={select} option={{animation:false,grid:{top:25,right:35,bottom:50,left:135},tooltip:{trigger:'item',formatter:(p:unknown)=>{const item=p as {seriesName:string;value:number[]};return `${item.seriesName}<br/>${clock(item.value[0])}`;}},xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),color:ink.axis},splitLine:{lineStyle:{color:ink.grid}}},yAxis:{type:'value',min:0,max:5,interval:1,axisLabel:{formatter:(v:number)=>topology.stations[v]?.name||'',color:ink.label},splitLine:{lineStyle:{color:ink.grid}}},dataZoom:[{type:'inside',xAxisIndex:0},{type:'slider',height:13,bottom:5,borderColor:'transparent',fillerColor:ink.zoom,handleSize:0}],series}}/>;
}

export function SpeedChart({profile,proposal=null,advice}:{profile:Profile|null;proposal?:Profile|null;advice?:SpeedAdvice}){
 const ink=useInk();
 if(!profile)return <div className="empty small">Профиль обновляется…</div>;
 if(!profile.points.length)return <div className="empty small">Профиль будет доступен после применения нового плана.</div>;
 const series:echarts.LineSeriesOption[]=[
  {name:'Ограничение',type:'line',showSymbol:false,data:profile.points.map(p=>[p[0],p[3]*3.6]),lineStyle:{color:ink.limit,type:'dashed',width:1.5},itemStyle:{color:ink.limit}},
  {name:'Активный профиль',type:'line',showSymbol:false,data:profile.points.map(p=>[p[0],p[2]*3.6]),lineStyle:{color:ink.active,width:2},itemStyle:{color:ink.active},areaStyle:{color:ink.active,opacity:.06},markLine:advice?{silent:true,symbol:'none',lineStyle:{color:ink.marker,type:'dotted'},label:{formatter:'Сейчас',color:ink.label},data:[{xAxis:advice.sim_time_s}]}:undefined,
   markPoint:advice?{symbol:'circle',symbolSize:8,label:{show:false},data:[{name:'Текущая скорость',coord:[advice.sim_time_s,advice.current_speed_mps*3.6]}]}:undefined},
 ];
 if(proposal)series.push({name:'Экономичный вариант',type:'line',showSymbol:false,data:proposal.points.map(p=>[p[0],p[2]*3.6]),lineStyle:{color:ink.eco,width:2,type:'dashed'},itemStyle:{color:ink.eco}});
 return <Chart height={245} option={{animation:false,legend:{top:4,textStyle:{fontSize:10,color:ink.label}},grid:{left:45,right:20,top:48,bottom:50},tooltip:{trigger:'axis',valueFormatter:v=>`${Number(v).toFixed(1)} км/ч`},xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),fontSize:10,color:ink.axis},splitLine:{show:false}},yAxis:{type:'value',name:'км/ч',nameTextStyle:{color:ink.axis},axisLabel:{fontSize:10,color:ink.axis},splitLine:{lineStyle:{color:ink.grid}}},dataZoom:[{type:'inside',xAxisIndex:0},{type:'slider',height:12,bottom:5}],series}}/>;
}

export function QualityChart({points}:{points:QualityPoint[]}) {
 const ink=useInk();
 if(!points.length)return <div className="empty small">Снимки качества ещё не сохранены.</div>;
 return <Chart height={210} option={{animation:false,grid:{left:42,right:22,top:20,bottom:34},
  tooltip:{trigger:'axis',valueFormatter:v=>Number(v).toFixed(1)+' / 100'},
  xAxis:{type:'value',axisLabel:{formatter:(s:number)=>clock(s).slice(0,-3),fontSize:10,color:ink.axis},splitLine:{show:false}},
  yAxis:{type:'value',min:0,max:100,axisLabel:{fontSize:10,color:ink.axis},splitLine:{lineStyle:{color:ink.grid}}},
  series:[{name:'Текущий индекс',type:'line',showSymbol:true,symbolSize:5,connectNulls:false,
   data:points.map(p=>[p.sim_time_s,p.index]),lineStyle:{color:ink.active,width:2},itemStyle:{color:ink.active}}]}}/>;
}
