// Rolling stock and stations for the 3D view, refined from the Rail3D starter kit.
// Local axes: +Y up, +Z vehicle front / track direction, origin on the track centre at rail-top height.
// Dimensions approximate 1520 mm-gauge stock (TE33A-style diesel, 12-132 gondola, 11-280 boxcar,
// 61-4440 coach); they are visual models, not engineering drawings.
import {MAT,meshBuilder,type MeshBuilder,type MeshData} from './mesh';

export type VehicleKind='locomotive'|'coal'|'boxcar'|'coach';
export type StationKind='station-small'|'station-terminal';

/** Length over couplers and bogie centre spacing, used to lay out consists along the track. */
export const VEHICLES:Record<VehicleKind,{length:number;bogies:number;name:string}>={
 locomotive:{length:22.4,bogies:13.3,name:'Тепловоз ТЭ33А'},
 coal:{length:13.9,bogies:8.65,name:'Полувагон с углём'},
 boxcar:{length:15.9,bogies:10.0,name:'Крытый вагон'},
 coach:{length:25.6,bogies:17.0,name:'Пассажирский вагон'},
};

const RAIL_X=.79;

/** Extrude a convex side profile [z,y] across the vehicle width. */
function extrudeX(b:MeshBuilder,profile:[number,number][],half:number,color:string,mat:number=MAT.paint){
 const n=profile.length;
 for(let i=0;i<n;i++){
  const [z1,y1]=profile[i],[z2,y2]=profile[(i+1)%n];
  b.quad([-half,y1,z1],[-half,y2,z2],[half,y2,z2],[half,y1,z1],color,mat);
 }
 for(let i=1;i<n-1;i++){
  b.triangle([half,profile[0][1],profile[0][0]],[half,profile[i][1],profile[i][0]],[half,profile[i+1][1],profile[i+1][0]],color,mat);
  b.triangle([-half,profile[0][1],profile[0][0]],[-half,profile[i+1][1],profile[i+1][0]],[-half,profile[i][1],profile[i][0]],color,mat);
 }
}

function wheelset(b:MeshBuilder,z:number,radius:number){
 b.cylinder(.085,2.0,[0,radius,z],'#2b2f33',MAT.metal,10,[0,0,Math.PI/2]);
 for(const s of [-1,1]){
  b.cylinder(radius,.13,[s*RAIL_X,radius,z],'#34383c',MAT.metal,18,[0,0,Math.PI/2]);
  b.cylinder(radius+.035,.03,[s*(RAIL_X-.08),radius,z],'#3c4044',MAT.metal,18,[0,0,Math.PI/2]);
  b.cylinder(radius*.42,.05,[s*(RAIL_X+.08),radius,z],'#8d969c',MAT.metal,12,[0,0,Math.PI/2]);
 }
}

/** Two-axle freight/passenger bogie (18-100 style): cast side frames, spring sets, bolster. */
function twoAxleBogie(b:MeshBuilder,z:number,radius=.475,wheelbase=1.85,passenger=false){
 for(const dz of [-wheelbase/2,wheelbase/2])wheelset(b,z+dz,radius);
 const frame=passenger?'#2a2e31':'#3a3027';
 for(const s of [-1,1]){
  const x=s*1.06;
  b.box([.16,.22,wheelbase+.9],[x,radius+.12,z],frame,MAT.matte);
  b.box([.16,.16,wheelbase*.62],[x,radius+.42,z],frame,MAT.matte);
  for(const dz of [-wheelbase/2,wheelbase/2]){
   b.box([.17,.42,.26],[x,radius+.22,z+dz*.62],frame,MAT.matte,[dz>0?-.55:.55,0,0]);
   b.box([.24,.24,.3],[s*1.02,radius,z+dz],'#30353a',MAT.metal);
  }
  for(const dz of passenger?[-.32,.32]:[-.22,0,.22])b.cylinder(.09,.36,[x,radius+.32,z+dz],'#9a8f70',MAT.metal,8);
 }
 b.box([2.25,.24,.42],[0,radius+.42,z],frame,MAT.matte);
 b.box([.5,.12,.5],[0,radius+.6,z],'#2a2e31',MAT.matte);
}

function coupler(b:MeshBuilder,z:number,sign:number,y=1.06){
 b.box([.32,.26,.72],[0,y,z+sign*.36],'#2b2f33',MAT.metal);
 b.box([.48,.34,.22],[0,y,z+sign*.78],'#24282b',MAT.metal);
 b.box([.14,.14,.4],[.45,y+.02,z+sign*.25],'#7d2a22',MAT.paint);
}

export function createLocomotive():MeshData{
 const b=meshBuilder();
 const C={blue:'#1a5a9e',navy:'#123f70',grey:'#d3d9dc',roof:'#9aa6ad',frame:'#22272b',yellow:'#f2c230',glass:'#18323f',grille:'#2f3a40',rail:'#e8e2c8',lamp:'#fff3c4',red:'#e3343a'};
 // Six-axle Co-Co bogies with axle boxes, coil springs and traction motors.
 for(const z of [-6.65,6.65]){
  for(const dz of [-1.85,0,1.85])wheelset(b,z+dz,.525);
  for(const s of [-1,1]){
   b.box([.2,.42,5.3],[s*1.1,.82,z],C.frame,MAT.matte);
   b.box([.2,.2,4.6],[s*1.1,1.12,z],C.frame,MAT.matte);
   for(const dz of [-1.85,0,1.85]){
    b.box([.3,.34,.42],[s*1.06,.56,z+dz],'#2c3237',MAT.metal);
    b.cylinder(.11,.34,[s*1.1,1.2,z+dz-.32],'#c2b26f',MAT.metal,8);
    b.cylinder(.11,.34,[s*1.1,1.2,z+dz+.32],'#c2b26f',MAT.metal,8);
   }
   b.box([.12,.18,.5],[s*1.22,.92,z-2.45],'#5a636a',MAT.metal);
  }
  for(const dz of [-1.85,0,1.85])b.cylinder(.34,1.2,[0,.62,z+dz+.45],'#2a3036',MAT.metal,10,[0,0,Math.PI/2]);
  b.box([2.5,.22,.6],[0,1.18,z+(z>0?2.7:-2.7)],C.frame,MAT.matte);
 }
 // Underframe, walkway and chamfered fuel tank between the bogies.
 b.box([2.92,.36,21.0],[0,1.45,0],C.frame,MAT.matte);
 b.box([3.26,.06,21.0],[0,1.66,0],'#6c767c',MAT.metal);
 b.prism([[-1.25,.82],[1.25,.82],[1.38,1.0],[1.38,1.24],[-1.38,1.24],[-1.38,1.0]],-3.2,3.2,'#1d2226',MAT.paint);
 b.box([.6,.24,1.2],[1.5,1.1,-3.9],'#2a3036',MAT.metal);
 // Long hood: navy base, yellow band, light-grey upper with chamfered roof edges.
 b.prism([[-1.32,1.69],[1.32,1.69],[1.32,2.5],[-1.32,2.5]],-9.55,3.62,C.blue,MAT.paint);
 b.box([2.68,.12,13.2],[0,2.53,-2.96],C.yellow,MAT.paint);
 b.prism([[-1.32,2.59],[1.32,2.59],[1.32,3.86],[1.04,4.12],[-1.04,4.12],[-1.32,3.86]],-9.55,3.62,C.grey,MAT.paint,C.grey);
 for(const s of [-1,1]){
  // Radiator and engine-room grilles, side access doors.
  for(const z of [-8.5,-7.1,-5.7])b.box([.03,1.0,1.2],[s*1.335,3.2,z],C.grille,MAT.metal);
  for(const z of [-3.9,-2.4,-.9,.6,2.1]){
   b.box([.02,1.12,1.18],[s*1.335,3.18,z],'#bfc6c9',MAT.paint);
   b.box([.03,.05,.16],[s*1.345,3.18,z+.42],'#4a5257',MAT.metal);
  }
  b.box([.03,.55,1.6],[s*1.335,2.05,-6.9],'#123461',MAT.paint);
  // Yellow handrails along the walkway.
  for(let z=-9.6;z<=3.4;z+=1.62)b.box([.04,.95,.04],[s*1.6,2.15,z],C.yellow,MAT.paint);
  b.box([.045,.045,13.1],[s*1.6,2.6,-3.1],C.yellow,MAT.paint);
  b.box([.045,.045,13.1],[s*1.6,2.15,-3.1],C.yellow,MAT.paint);
  // Steps at each end.
  for(const z of [-10.1,9.75])for(let i=0;i<3;i++)b.box([.42,.05,.55],[s*(1.36-i*.04),.62+i*.33,z],'#8a9399',MAT.metal);
 }
 // Cab: sloped windshield, side windows, roof air-conditioner and horn.
 extrudeX(b,[[3.62,1.69],[8.02,1.69],[8.02,3.02],[7.58,4.28],[3.62,4.38]],1.55,C.grey);
 extrudeX(b,[[3.6,1.68],[8.04,1.68],[8.04,2.5],[3.6,2.5]],1.57,C.blue);
 b.box([3.16,.12,4.44],[0,2.53,5.82],C.yellow,MAT.paint);
 const glassZ=(y:number)=>8.02-(y-3.02)/1.26*.44+.025;
 for(const [l,r] of [[-1.34,-.08],[.08,1.34]]){
  b.quad([l,3.12,glassZ(3.12)],[r,3.12,glassZ(3.12)],[r,4.12,glassZ(4.12)],[l,4.12,glassZ(4.12)],C.glass,MAT.glass);
 }
 b.quad([-.06,3.1,glassZ(3.1)+.01],[.06,3.1,glassZ(3.1)+.01],[.06,4.15,glassZ(4.15)+.01],[-.06,4.15,glassZ(4.15)+.01],C.grey,MAT.paint);
 for(const s of [-1,1]){
  b.box([.03,.9,1.5],[s*1.565,3.45,6.55],C.glass,MAT.glass);
  b.box([.03,.9,.85],[s*1.565,3.45,4.75],C.glass,MAT.glass);
  b.box([.035,.06,2.6],[s*1.57,2.96,5.6],'#9aa4aa',MAT.metal);
  b.box([.03,1.9,.75],[s*1.565,2.75,3.95],'#b9c1c5',MAT.paint);
  b.box([.12,.22,.1],[s*1.63,3.95,7.6],'#202528',MAT.metal);
 }
 b.box([1.4,.32,1.5],[0,4.5,5.2],'#b9c2c7',MAT.metal);
 b.box([1.1,.05,1.2],[0,4.68,5.2],C.grille,MAT.metal);
 for(const dx of [-.18,.18])b.cylinder(.07,.34,[dx,4.48,7.0],'#c8ced1',MAT.metal,8,[Math.PI/2,0,0],.12);
 b.cylinder(.02,.7,[.8,4.7,4.3],'#202528',MAT.metal,6);
 // Short nose with yellow face, headlights and ditch lights.
 extrudeX(b,[[8.02,1.69],[10.0,1.69],[10.0,2.82],[9.62,3.04],[8.02,3.04]],1.42,C.blue);
 b.box([2.86,.62,.03],[0,2.45,10.015],C.yellow,MAT.paint);
 b.box([2.86,.1,1.98],[0,2.53,9.01],C.yellow,MAT.paint);
 for(const s of [-1,1]){
  b.box([.34,.24,.05],[s*.95,2.2,10.04],C.lamp,MAT.lamp);
  b.box([.22,.16,.05],[s*1.15,1.82,10.04],C.lamp,MAT.lamp);
  b.box([.03,.9,.03],[s*1.4,2.3,10.06],C.yellow,MAT.paint);
 }
 b.box([.6,.24,.08],[0,4.18,7.62],C.lamp,MAT.lamp);
 // Pilot (snow plough) and couplers.
 b.quad([-1.45,.25,10.9],[1.45,.25,10.9],[1.45,1.2,10.35],[-1.45,1.2,10.35],'#2c3236',MAT.metal);
 b.quad([1.45,.25,10.9],[-1.45,.25,10.9],[-1.45,1.2,10.35],[1.45,1.2,10.35],'#2c3236',MAT.metal);
 b.box([3.0,.34,.34],[0,1.16,10.25],C.frame,MAT.matte);
 b.box([3.0,.34,.34],[0,1.16,-10.25],C.frame,MAT.matte);
 coupler(b,10.42,1);coupler(b,-10.42,-1);
 // Rear: dark grille panel and red marker lamps.
 b.box([2.2,1.3,.03],[0,3.2,-9.565],C.grille,MAT.metal);
 for(let i=0;i<6;i++)b.box([2.2,.03,.04],[0,2.62+i*.22,-9.58],'#5b666c',MAT.metal);
 for(const s of [-1,1]){b.box([.2,.2,.05],[s*1.0,2.1,-9.58],C.red,MAT.lamp);b.box([.32,.22,.05],[s*.95,2.25,-9.6],C.lamp,MAT.lamp);}
 // Roof: three radiator fans, exhaust stack, dynamic-brake hatch.
 for(const z of [-8.3,-6.8,-5.3]){
  b.cylinder(.62,.1,[0,4.16,z],'#1f2427',MAT.metal,18);
  b.cylinder(.66,.04,[0,4.22,z],'#7b868c',MAT.metal,18);
  for(const a of [0,Math.PI/4,Math.PI/2,Math.PI*3/4])b.box([1.1,.02,.08],[0,4.24,z],'#59646a',MAT.metal,[0,a,0]);
 }
 b.box([.7,.36,1.0],[0,4.26,-2.6],'#2a3034',MAT.metal);
 b.box([.5,.08,.8],[0,4.47,-2.6],'#121517',MAT.matte);
 b.box([2.0,.18,2.6],[0,4.2,.8],'#a6b1b7',MAT.metal);
 for(let i=0;i<5;i++)b.box([1.9,.03,.06],[0,4.31,-.2+i*.5],'#6a757b',MAT.metal);
 return b.finish();
}

/** Shared freight underframe: centre sill, bogies, couplers, end steps and handholds. */
function freightUnderframe(b:MeshBuilder,length:number,bogies:number,body:string){
 b.box([2.9,.28,length-1.2],[0,1.2,0],'#2c2522',MAT.matte);
 b.box([.5,.38,length-.8],[0,1.06,0],'#2c2522',MAT.matte);
 for(const z of [-bogies/2,bogies/2])twoAxleBogie(b,z);
 const half=length/2-.6;
 for(const s of [-1,1]){
  b.box([2.95,.3,.24],[0,1.16,s*half],body,MAT.paint);
  coupler(b,s*half,s);
  for(const x of [-1,1]){
   b.box([.4,.05,.3],[x*1.3,.62,s*(half-.2)],'#5a5048',MAT.metal);
   b.box([.04,.62,.04],[x*1.47,.95,s*(half-.2)],'#5a5048',MAT.metal);
  }
 }
 b.cylinder(.2,.7,[.7,.95,-1.6],'#2a2523',MAT.metal,10,[Math.PI/2,0,0]);
}

export function createCoalWagon():MeshData{
 const b=meshBuilder(),L=VEHICLES.coal.length,body='#7a3524',dark='#5c2618',rib='#6a2d1e';
 freightUnderframe(b,L,VEHICLES.coal.bogies,body);
 const half=L/2-.75;
 b.box([3.02,.16,L-1.4],[0,1.38,0],dark,MAT.paint);
 for(const s of [-1,1]){
  // Side walls with external stakes, top chord and lower sill.
  b.box([.08,2.05,L-1.5],[s*1.5,2.45,0],body,MAT.paint);
  b.box([.16,.13,L-1.42],[s*1.53,3.5,0],rib,MAT.paint);
  b.box([.14,.16,L-1.42],[s*1.53,1.45,0],dark,MAT.paint);
  for(let z=-half+.25;z<=half-.24;z+=(L-2)/8)b.box([.07,2.02,.12],[s*1.575,2.45,z],rib,MAT.paint);
  for(let i=0;i<7;i++)b.box([.06,.32,.6],[s*1.55,1.62,-half+1.1+i*1.55],dark,MAT.paint);
  // Ends: wall, ribs, handbrake wheel on one end.
  b.box([3.0,2.05,.08],[0,2.45,s*half],body,MAT.paint);
  for(const x of [-1.05,-.35,.35,1.05])b.box([.1,2.0,.07],[x,2.45,s*(half+.07)],rib,MAT.paint);
  b.box([3.08,.13,.16],[0,3.5,s*half],rib,MAT.paint);
  // Faint stencil panels in place of lettering.
  b.box([.012,.32,1.6],[s*1.585,2.75,-3.2],'#d8d2c4',MAT.matte);
  b.box([.012,.18,.9],[s*1.585,2.3,-3.55],'#d8d2c4',MAT.matte);
 }
 b.cylinder(.22,.04,[-.9,2.9,half+.13],'#2a2523',MAT.metal,12,[Math.PI/2,0,0]);
 // Heaped coal load: a fixed faceted surface, higher along the centre line.
 const nx=6,nz=20,point=(i:number,j:number):[number,number,number]=>{
  const x=-1.42+i*2.84/nx,z=-half+.12+j*(2*half-.24)/nz;
  const edge=Math.min(i,nx-i)/(nx/2),end=Math.min(j,nz-j)/3;
  return [x,3.28+.42*Math.min(1,edge)*Math.min(1,end)+.06*Math.sin(i*7.1+j*3.3),z];
 };
 const shades=['#1c1f21','#25292b','#141618','#2d3134'];
 for(let i=0;i<nx;i++)for(let j=0;j<nz;j++){
  const p=point(i,j),q=point(i+1,j),r=point(i+1,j+1),s=point(i,j+1);
  b.triangle(p,s,r,shades[(i*3+j)%4],MAT.paint);b.triangle(p,r,q,shades[(i+j*5+1)%4],MAT.paint);
 }
 return b.finish();
}

export function createBoxcar():MeshData{
 const b=meshBuilder(),L=VEHICLES.boxcar.length,body='#6e2f22',rib='#5f281c',dark='#4e2117';
 freightUnderframe(b,L,VEHICLES.boxcar.bogies,body);
 const half=L/2-.75;
 b.prism([[-1.56,1.34],[1.56,1.34],[1.56,4.02],[-1.56,4.02]],-half,half,body,MAT.paint,body);
 // Curved roof with running board.
 const arc:[number,number][]=[];
 for(let i=0;i<=10;i++){const a=i/10*Math.PI;arc.push([1.64*Math.cos(a),4.02+.4*Math.sin(a)]);}
 b.prism(arc,-half-.05,half+.05,'#5d5650',MAT.paint,'#5d5650');
 b.box([.5,.04,L-1.6],[0,4.45,0],'#4a4440',MAT.metal);
 for(const s of [-1,1]){
  // Ribbed sides, sill and top rail.
  for(let z=-half+.4;z<=half-.39;z+=.86)if(Math.abs(z)>1.7)b.box([.06,2.6,.09],[s*1.59,2.68,z],rib,MAT.paint);
  b.box([.1,.14,L-1.5],[s*1.6,1.4,0],dark,MAT.paint);
  b.box([.08,.1,L-1.5],[s*1.6,4.0,0],rib,MAT.paint);
  // Sliding door on its rails, with locks and handles.
  b.box([.07,2.5,3.1],[s*1.62,2.64,.35],'#7b3627',MAT.paint);
  for(let z=-1.0;z<=1.75;z+=.45)b.box([.035,2.4,.05],[s*1.66,2.64,z],rib,MAT.paint);
  b.box([.12,.1,3.6],[s*1.66,3.96,.35],'#3a3530',MAT.metal);
  b.box([.12,.1,3.6],[s*1.66,1.42,.35],'#3a3530',MAT.metal);
  b.box([.05,.5,.06],[s*1.7,2.5,-1.05],'#c9b46a',MAT.metal);
  b.box([.012,.3,1.5],[s*1.63,3.2,-4.6],'#d8d2c4',MAT.matte);
  b.box([.012,.18,1.0],[s*1.63,2.75,-4.85],'#d8d2c4',MAT.matte);
  // Ends with ladder rungs.
  for(const x of [-1.1,-.4,.4,1.1])b.box([.08,2.6,.06],[x,2.68,s*(half+.03)],rib,MAT.paint);
  for(let i=0;i<6;i++)b.box([.42,.03,.03],[1.2,1.6+i*.42,s*(half+.12)],'#3a3530',MAT.metal);
 }
 return b.finish();
}

export function createCoach():MeshData{
 const b=meshBuilder(),L=VEHICLES.coach.length,half=L/2-.55;
 const C={body:'#1c4c86',band:'#e9edef',stripe:'#f0c23b',roof:'#7c868d',glass:'#16252e'};
 for(const z of [-VEHICLES.coach.bogies/2,VEHICLES.coach.bogies/2])twoAxleBogie(b,z,.475,2.4,true);
 b.box([2.9,.3,L-1.4],[0,1.24,0],'#25292c',MAT.matte);
 for(const [z,w] of [[-4,2.6],[3.5,3.2],[-.5,1.6]] as [number,number][])b.box([1.9,.5,w],[0,.92,z],'#2b3033',MAT.metal);
 // Body with rounded cant rails and roof.
 b.prism([[-1.6,1.36],[1.6,1.36],[1.63,3.5],[1.5,4.12],[1.05,4.42],[-1.05,4.42],[-1.5,4.12],[-1.63,3.5]],-half,half,C.body,MAT.paint,C.body);
 b.prism([[-1.08,4.4],[1.08,4.4],[.9,4.52],[-.9,4.52]],-half+.3,half-.3,C.roof,MAT.metal);
 for(const s of [-1,1]){
  b.box([.03,.86,L-2.4],[s*1.64,2.95,0],C.band,MAT.paint);
  b.box([.03,.1,L-2.4],[s*1.645,2.38,0],C.stripe,MAT.paint);
  // Window row (lit at night) and end doors.
  for(let i=0;i<10;i++)b.box([.035,.72,1.3],[s*1.66,2.98,-half+3.3+i*1.95],C.glass,MAT.glass);
  for(const z of [-half+1.15,half-1.15]){
   b.box([.035,2.25,.92],[s*1.655,2.5,z],'#244f84',MAT.paint);
   b.box([.04,.62,.5],[s*1.67,3.1,z],C.glass,MAT.glass);
  }
  b.box([.04,.04,L-1.6],[s*1.62,1.4,0],'#3c4246',MAT.metal);
  // Gangway bellows and buffers at each end.
  b.box([2.0,2.6,.35],[0,2.7,s*(half+.17)],'#262a2d',MAT.matte);
  coupler(b,s*(half+.1),s,1.06);
 }
 return b.finish();
}

function gableRoof(b:MeshBuilder,cx:number,y:number,cz:number,width:number,length:number,height:number,color:string){
 const a:[number,number,number]=[cx-width/2,y,cz-length/2],d:[number,number,number]=[cx+width/2,y,cz-length/2];
 const e:[number,number,number]=[cx-width/2,y,cz+length/2],f:[number,number,number]=[cx+width/2,y,cz+length/2];
 const n:[number,number,number]=[cx,y+height,cz-length/2],s:[number,number,number]=[cx,y+height,cz+length/2];
 b.quad(a,e,s,n,color,MAT.paint);b.quad(n,s,f,d,color,MAT.paint);
 b.triangle(a,n,d,color,MAT.paint);b.triangle(e,f,s,color,MAT.paint);
}

/** Station on the +X side of the track: platform, canopy, building, benches, lamps. */
export function createStation(kind:StationKind):MeshData{
 const b=meshBuilder(),big=kind==='station-terminal',L=big?220:140;
 const W=big?8:6,px=2.2+W/2,top=1.1;
 b.box([W,top,L],[px,top/2-.2,0],'#9b9d97',MAT.matte);
 b.box([W,.06,L],[px,top-.17,0],'#c4c3bb',MAT.matte);
 b.box([.25,.02,L-1],[2.45,top-.13,0],'#ecc338',MAT.paint);
 b.box([.5,.03,L],[2.3,top-.14,0],'#e3e0d6',MAT.matte);
 const canopy=big?120:56,cx=big?6:5.2;
 for(let z=-canopy/2+3;z<=canopy/2-2;z+=8){
  b.box([.2,3.6,.2],[cx+1.3,top+1.6,z],'#3f5964',MAT.metal);
  b.box([big?6.2:5,.16,.18],[cx,top+3.42,z],'#5f818d',MAT.metal);
 }
 b.box([big?6.6:5.4,.18,canopy],[cx,top+3.6,0],'#2f6e7c',MAT.paint);
 b.box([big?6.7:5.5,.06,canopy],[cx,top+3.72,0],'#9fb9b6',MAT.metal);
 const bx=big?17:13.5,bw=big?13:8,bl=big?42:20,bh=big?8:4.6;
 b.box([bw+.8,.3,bl+.8],[bx,top-.05,0],'#8f9792',MAT.matte);
 b.box([bw,bh,bl],[bx,top+bh/2,0],'#e3dcc2',MAT.paint);
 b.box([bw+.1,.5,bl+.1],[bx,top+.3,0],'#9aa79d',MAT.paint);
 gableRoof(b,bx,top+bh,0,bw+1.2,bl+1.2,big?1.8:1.2,'#3f6878');
 for(let z=-bl/2+2.2;z<bl/2-1.4;z+=2.8){
  for(const x of [bx-bw/2-.03,bx+bw/2+.03]){
   b.box([.06,big?3.4:1.7,1.6],[x,top+(big?3.6:2.4),z],'#1f3c4a',MAT.glass);
   b.box([.08,.08,1.7],[x,top+(big?1.85:1.5),z],'#c8d2cc',MAT.paint);
  }
 }
 b.box([.12,2.6,2.6],[bx-bw/2-.08,top+1.3,0],'#1c4a62',MAT.glass);
 b.box([.14,.8,5],[bx-bw/2-.14,top+bh-.7,0],'#13617f',MAT.paint);
 if(big){
  b.box([3.4,11,4.4],[bx-3,top+5.5,0],'#e6dec4',MAT.paint);
  b.box([3.9,.3,4.9],[bx-3,top+11.1,0],'#3f6878',MAT.paint);
  b.cylinder(.85,.06,[bx-4.73,top+9,0],'#22333d',MAT.metal,20,[0,0,Math.PI/2]);
  b.cylinder(.74,.07,[bx-4.76,top+9,0],'#efe4c0',MAT.lamp,20,[0,0,Math.PI/2]);
 }
 for(let z=-L/2+10;z<L/2-8;z+=big?24:20){
  b.box([.12,4.6,.12],[px+W/2-.6,top+2.3,z],'#4f646d',MAT.metal);
  b.box([.9,.1,.3],[px+W/2-.95,top+4.6,z],'#5f737c',MAT.metal);
  b.box([.5,.05,.22],[px+W/2-1.2,top+4.53,z],'#fff2c2',MAT.lamp);
  b.box([.55,.1,2.2],[px+1.2,top+.45,z+4],'#8a6f4c',MAT.paint);
  b.box([.1,.5,2.2],[px+1.45,top+.7,z+4],'#9c7f58',MAT.paint);
 }
 for(const s of [-1,1]){
  b.box([.1,2,.1],[px+.8,top+1,s*(L/2-14)],'#506670',MAT.metal);
  b.box([.12,.7,1.9],[px+.8,top+2.2,s*(L/2-14)],'#14596f',MAT.glass);
 }
 return b.finish();
}

export function createShadow(kind:VehicleKind):MeshData{
 const b=meshBuilder();
 b.shadow(kind==='coach'?3.1:3.0,VEHICLES[kind].length-.6);
 return b.finish();
}
