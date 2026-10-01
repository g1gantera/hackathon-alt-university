import type {ScenarioSettings,QualityWeights} from './types.ts';

export const qualityWeightKeys:(keyof QualityWeights)[]=['schedule','capacity','energy','conflicts','arrival_accuracy'];

export function weightPercentages(weights:QualityWeights){
 const sum=qualityWeightKeys.reduce((value,key)=>value+weights[key],0);
 return Object.fromEntries(qualityWeightKeys.map(key=>[key,sum>0?100*weights[key]/sum:0])) as QualityWeights;
}

export const numericSettings={
 passenger_weight:{label:'Вес пассажирских поездов',min:.01,max:20,step:.5},
 freight_weight:{label:'Вес грузовых поездов',min:.01,max:20,step:.5},
 delay_norm_s:{label:'Норма отклонения отправлений, с',min:1,max:1000000,step:60},
 energy_norm_kwh:{label:'Норма перерасхода энергии, кВт·ч',min:1,max:10000000,step:100},
 arrival_tolerance_s:{label:'Допуск точности прибытия, с',min:0,max:3600,step:30},
 conflict_penalty:{label:'Штраф за одно нарушение',min:.01,max:1000,step:.25},
} as const;

export function settingsError(settings:ScenarioSettings):string|null{
 if(qualityWeightKeys.some(key=>!Number.isFinite(settings.quality_weights[key])||settings.quality_weights[key]<0||settings.quality_weights[key]>1000))return 'Веса должны быть числами от 0 до 1000.';
 if(qualityWeightKeys.every(key=>settings.quality_weights[key]===0))return 'Хотя бы один вес должен быть больше нуля.';
 const {quality_threshold_attention:attention,quality_threshold_normal:normal}=settings;
 if(!Number.isFinite(attention)||!Number.isFinite(normal)||attention<0||normal>100||attention>=normal)return 'Пороги: 0 ≤ «Внимание» < «Норма» ≤ 100.';
 for(const key of Object.keys(numericSettings) as (keyof typeof numericSettings)[]){
  const {label,min,max}=numericSettings[key],value=settings[key];
  if(!Number.isFinite(value)||value<min||value>max)return `${label}: допустимо от ${min} до ${max}.`;
 }
 return null;
}
