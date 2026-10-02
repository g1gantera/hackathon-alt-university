// Procedural mesh builder (ported from the Rail3D starter kit). Y is up, units are metres.
// Each vertex carries RGB plus a material code in the fourth colour channel.

export type Vec3=[number,number,number];
export interface MeshData{positions:Float32Array;normals:Float32Array;colors:Float32Array;triangles:number}

/** Material codes read by the fragment shader. */
export const MAT={matte:0,paint:.5,metal:1,lamp:2,glass:3,shadow:4} as const;

export function meshBuilder(){
 const positions:number[]=[],normals:number[]=[],colors:number[]=[];
 const cache=new Map<string,Vec3>();
 const linear=(hex:string):Vec3=>{
  let rgb=cache.get(hex);
  if(!rgb){
   const n=parseInt(hex.slice(1),16);
   rgb=[n>>16&255,n>>8&255,n&255].map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}) as Vec3;
   cache.set(hex,rgb);
  }
  return rgb;
 };
 function triangle(a:Vec3,b:Vec3,c:Vec3,color:string,mat=0,alpha?:[number,number,number]){
  const u=[b[0]-a[0],b[1]-a[1],b[2]-a[2]],v=[c[0]-a[0],c[1]-a[1],c[2]-a[2]];
  let n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
  const length=Math.hypot(n[0],n[1],n[2]);
  if(length<1e-10)return;
  n=n.map(q=>q/length);
  const rgb=linear(color);
  [a,b,c].forEach((p,i)=>{positions.push(...p);normals.push(...n);colors.push(...rgb,alpha?alpha[i]:mat);});
 }
 function quad(a:Vec3,b:Vec3,c:Vec3,d:Vec3,color:string,mat=0){triangle(a,b,c,color,mat);triangle(a,c,d,color,mat);}
 function transform(p:Vec3,center:Vec3,rotation:Vec3):Vec3{
  let [x,y,z]=p;
  const [rx,ry,rz]=rotation;
  let c=Math.cos(rx),s=Math.sin(rx);[y,z]=[y*c-z*s,y*s+z*c];
  c=Math.cos(ry);s=Math.sin(ry);[x,z]=[x*c+z*s,-x*s+z*c];
  c=Math.cos(rz);s=Math.sin(rz);[x,y]=[x*c-y*s,x*s+y*c];
  return [x+center[0],y+center[1],z+center[2]];
 }
 function box(size:Vec3,center:Vec3,color:string,mat=0,rotation:Vec3=[0,0,0]){
  const [w,h,l]=size.map(v=>v/2);
  const p=([[-w,-h,-l],[w,-h,-l],[w,h,-l],[-w,h,-l],[-w,-h,l],[w,-h,l],[w,h,l],[-w,h,l]] as Vec3[]).map(v=>transform(v,center,rotation));
  for(const f of [[0,3,2,1],[4,5,6,7],[0,4,7,3],[1,2,6,5],[3,7,6,2],[0,1,5,4]])quad(p[f[0]],p[f[1]],p[f[2]],p[f[3]],color,mat);
 }
 /** Axis along local Y before rotation; [0,0,PI/2] lays it across the track (wheels, axles). */
 function cylinder(radius:number,height:number,center:Vec3,color:string,mat=0,segments=14,rotation:Vec3=[0,0,0],topRadius=radius){
  const top=transform([0,height/2,0],center,rotation),bottom=transform([0,-height/2,0],center,rotation);
  for(let i=0;i<segments;i++){
   const a=i/segments*Math.PI*2,b=(i+1)/segments*Math.PI*2;
   const p=([[radius*Math.cos(a),-height/2,radius*Math.sin(a)],[topRadius*Math.cos(a),height/2,topRadius*Math.sin(a)],
    [topRadius*Math.cos(b),height/2,topRadius*Math.sin(b)],[radius*Math.cos(b),-height/2,radius*Math.sin(b)]] as Vec3[]).map(v=>transform(v,center,rotation));
   quad(p[0],p[1],p[2],p[3],color,mat);triangle(top,p[2],p[1],color,mat);triangle(bottom,p[0],p[3],color,mat);
  }
 }
 /** Extrude a convex cross-section [x,y] along Z from z0 to z1 (car bodies, chamfered hoods, curved roofs). */
 function prism(profile:[number,number][],z0:number,z1:number,color:string,mat=0,capColor=color,caps=true){
  const n=profile.length;
  for(let i=0;i<n;i++){
   const [x1,y1]=profile[i],[x2,y2]=profile[(i+1)%n];
   quad([x1,y1,z0],[x2,y2,z0],[x2,y2,z1],[x1,y1,z1],color,mat);
  }
  if(!caps)return;
  for(let i=1;i<n-1;i++){
   triangle([profile[0][0],profile[0][1],z1],[profile[i][0],profile[i][1],z1],[profile[i+1][0],profile[i+1][1],z1],capColor,mat);
   triangle([profile[0][0],profile[0][1],z0],[profile[i+1][0],profile[i+1][1],z0],[profile[i][0],profile[i][1],z0],capColor,mat);
  }
 }
 /** Soft contact shadow: a flat rectangle that fades out towards its edges. */
 function shadow(width:number,length:number,y=.02){
  const hw=width/2,hl=length/2,f=1.6;
  const rings:[number,number,number][]=[[hw+f,hl+f,0],[hw,hl,.55]];
  const corners=(w:number,l:number):Vec3[]=>[[-w,y,-l],[w,y,-l],[w,y,l],[-w,y,l]];
  const outer=corners(rings[0][0],rings[0][1]),inner=corners(rings[1][0],rings[1][1]);
  for(let i=0;i<4;i++){
   const j=(i+1)%4;
   triangle(outer[i],inner[i],inner[j],'#000000',0,[MAT.shadow,MAT.shadow+.55,MAT.shadow+.55]);
   triangle(outer[i],inner[j],outer[j],'#000000',0,[MAT.shadow,MAT.shadow+.55,MAT.shadow]);
  }
  triangle(inner[0],inner[1],inner[2],'#000000',0,[MAT.shadow+.55,MAT.shadow+.55,MAT.shadow+.55]);
  triangle(inner[0],inner[2],inner[3],'#000000',0,[MAT.shadow+.55,MAT.shadow+.55,MAT.shadow+.55]);
 }
 return {box,cylinder,prism,triangle,quad,shadow,finish():MeshData{
  return {positions:new Float32Array(positions),normals:new Float32Array(normals),colors:new Float32Array(colors),triangles:positions.length/9};
 }};
}
export type MeshBuilder=ReturnType<typeof meshBuilder>;
