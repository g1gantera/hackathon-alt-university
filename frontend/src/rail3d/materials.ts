import type {Train} from '../types';
import {VEHICLES,type VehicleKind} from './models';

export const VS=`#version 300 es
layout(location=0) in vec3 aPos;layout(location=1) in vec3 aNormal;layout(location=2) in vec4 aColor;layout(location=3) in vec4 aPose;
uniform mat4 uVP;uniform vec3 uOffset;
out vec3 vColor;out vec3 vNormal;out vec3 vPos;out float vMat;out float vHeight;
void main(){
 float c=cos(aPose.w),s=sin(aPose.w);
 vec3 p=vec3(c*aPos.x+s*aPos.z,aPos.y,-s*aPos.x+c*aPos.z)+aPose.xyz+uOffset;
 vNormal=vec3(c*aNormal.x+s*aNormal.z,aNormal.y,-s*aNormal.x+c*aNormal.z);
 vColor=aColor.rgb;vMat=aColor.a;vPos=p;vHeight=aPos.y;
 gl_Position=uVP*vec4(p,1.);
}`;
export const FS=`#version 300 es
precision highp float;
in vec3 vColor;in vec3 vNormal;in vec3 vPos;in float vMat;in float vHeight;
uniform vec3 uSun,uSunColor,uSky,uGround,uFog,uEye,uOrigin;uniform float uFogDensity,uNight,uAO,uGroundTex,uShadow;
out vec4 outColor;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
void main(){
 if(vMat>3.9){outColor=vec4(0.,0.,0.,(vMat-4.)*uShadow);return;}
 vec3 n=normalize(vNormal);if(!gl_FrontFacing)n=-n;
 vec3 v=normalize(uEye-vPos),base=vColor;
 if(uGroundTex>.5){
  // Steppe: large patches of dry grass with finer variation.
  vec2 w=vPos.xz+uOrigin.xz;
  float a=noise(w*.004),b=noise(w*.03),c=noise(w*.25);
  base=mix(vec3(.2,.19,.07),vec3(.3,.27,.11),a);
  base=mix(base,vec3(.16,.18,.08),smoothstep(.55,.8,b)*.6);
  base*=.85+.3*c;
 }
 float diffuse=max(dot(n,uSun),0.);
 vec3 ambient=mix(uGround,uSky,.5+.5*n.y);
 float ao=uAO>.5?mix(.42,1.,smoothstep(.15,2.2,vHeight)):1.;
 float spec=0.;
 vec3 h=normalize(uSun+v);
 if(vMat>.25&&vMat<1.5){float metal=step(.75,vMat);spec=pow(max(dot(n,h),0.),mix(28.,72.,metal))*mix(.18,.6,metal);}
 vec3 color=base*(ambient*.62*ao+uSunColor*diffuse*mix(.8,1.,ao))+uSunColor*spec*ao;
 if(vMat>1.5&&vMat<2.5)color=base*(.9+uNight*3.2);
 if(vMat>2.5&&vMat<3.5){
  float fresnel=pow(1.-max(dot(n,v),0.),3.);
  color=mix(base*.5,uSky*1.2,.2+.6*fresnel)+uSunColor*pow(max(dot(n,h),0.),90.)*.8;
  color=mix(color,vec3(1.,.78,.45)*.85,uNight*.7);
 }
 float d=length(vPos-uEye);
 color=mix(color,uFog,1.-exp(-pow(d*uFogDensity,2.)));
 outColor=vec4(pow(max(color,vec3(0.)),vec3(1./2.2)),1.);
}`;
export const THEMES={
 light:{sun:[-.42,.78,.46],sunColor:[1.18,1.1,.98],sky:[.42,.52,.62],ground:[.3,.26,.18],fog:[.62,.7,.78],top:[.18,.33,.58],night:0},
 dark:{sun:[.35,.7,-.6],sunColor:[.2,.24,.34],sky:[.035,.05,.08],ground:[.02,.02,.025],fog:[.025,.035,.055],top:[.004,.007,.016],night:1},
};

/** Consist behind the head of the train: freight runs double-headed, passenger trains haul coaches. */
export function consistFor(train:Train):{kind:VehicleKind;offset:number}[]{
 const kinds:VehicleKind[]=train.type==='passenger'?['locomotive']:['locomotive','locomotive'];
 const body=train.type==='passenger'?'coach':'coal';
 let length=kinds.reduce((s,k)=>s+VEHICLES[k].length,0);
 const blockSwitch=train.id.charCodeAt(train.id.length-1)%2?.45:.7;
 while(length<train.length_m-8){
  const kind:VehicleKind=body==='coach'?'coach':length/train.length_m<blockSwitch?'coal':'boxcar';
  kinds.push(kind);length+=VEHICLES[kind].length;
 }
 let offset=0;
 return kinds.map((kind,i)=>{if(i)offset+=(VEHICLES[kinds[i-1]].length+VEHICLES[kind].length)/2;return {kind,offset};});
}
