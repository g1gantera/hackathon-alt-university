/** Presentation-only observations; the caller owns simulation and dispatch state. */
export interface TrainMotionFrame {
 epoch: string;
 sim_time_s: number;
 running: boolean;
 speed: number;
 trains: ReadonlyArray<{id:string;position_m:number;direction:number;status:string}>;
 stream_id?: string;
 update_interval_ms?: number;
}


export interface TrainMotionSample {position:number;animating:boolean}
interface Segment {
 from:number;target:number;start:number;end:number;slope:number;
 direction:number;settling:boolean;
}
const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
const EPSILON=1e-7;

/** A monotone Hermite segment, ending at rest at the last observed position. */
function evaluate(segment:Segment,now:number){
 const duration=segment.end-segment.start;
 if(duration<=0||now>=segment.end)return {position:segment.target,velocity:0,animating:false};
 const t=clamp((now-segment.start)/duration,0,1),t2=t*t,t3=t2*t;
 const fraction=(-2*t3+3*t2)+segment.slope*(t3-2*t2+t);
 const delta=segment.target-segment.from;
 return {
  position:segment.from+delta*clamp(fraction,0,1),
  velocity:delta/duration*((6*t-6*t2)+segment.slope*(3*t2-4*t+1)),
  animating:Math.abs(delta)>EPSILON,
 };
}

/**
 * Smooth presentation of authoritative chainage, never a second simulator.
 * Retargets start at the currently displayed position (and velocity when the
 * monotonicity bound permits), so packet jitter and selection renders cannot
 * restart a train from the previous packet. No sample passes the latest target.
 * All time arguments are monotonic browser milliseconds, e.g. performance.now().
 */
export class TrainMotionBuffer {
 private segments=new Map<string,Segment>();
 private epoch?:string;
 private stream?:string;
 private simTime?:number;
 private lastAdvanceAt?:number;
 private lastPushAt=0;
 private cadence=500;
 private running?:boolean;

 push(snapshot:TrainMotionFrame,now:number):void {
  if(!Number.isFinite(now)||!Number.isFinite(snapshot.sim_time_s))return;
  now=Math.max(this.lastPushAt,now);
  const elapsed=this.simTime===undefined?0:snapshot.sim_time_s-this.simTime;
  const stream=snapshot.stream_id;
  const discontinuity=this.epoch===undefined||this.epoch!==snapshot.epoch||elapsed<0||elapsed>=600||
   (!!this.stream&&!!stream&&this.stream!==stream);
  // Paused heartbeats must not make the first post-resume packet look delayed.
  if(!snapshot.running||this.running===false&&snapshot.running)this.lastAdvanceAt=now;
  this.running=snapshot.running;
  if(discontinuity){
   this.segments.clear();this.cadence=500;this.lastAdvanceAt=now;
  }else if(elapsed>0){
   if(this.lastAdvanceAt!==undefined){
    const interval=now-this.lastAdvanceAt;
    if(interval>0)this.cadence=clamp(this.cadence*.75+clamp(interval,250,1100)*.25,250,1100);
   }
   this.lastAdvanceAt=now;
  }
  this.epoch=snapshot.epoch;this.stream=stream;this.simTime=snapshot.sim_time_s;this.lastPushAt=now;
  const ids=new Set(snapshot.trains.map(train=>train.id));
  for(const id of this.segments.keys())if(!ids.has(id))this.segments.delete(id);
  for(const train of snapshot.trains){
   const target=train.position_m;
   if(!Number.isFinite(target))continue;
   const direction=train.direction<0?-1:1;
   const settling=!snapshot.running||train.status!=='moving';
   const previous=this.segments.get(train.id);
   // Direction changes and authoritative corrections are discontinuities, not
   // an opportunity to extrapolate a fictitious manoeuvre between observations.
   if(!previous||discontinuity||previous.direction!==direction||
      (target-previous.target)*direction<-EPSILON){
    this.segments.set(train.id,{from:target,target,start:now,end:now,slope:0,direction,settling});
    continue;
   }
   const current=evaluate(previous,now);
   if(Math.abs(target-previous.target)<=EPSILON){
    // Duplicate snapshots and paused heartbeats retain their original deadline.
    // A newly requested pause/arrival may only shorten a pending long tween.
    if(settling&&!previous.settling&&previous.end-now>500){
     this.retarget(train.id,current.position,target,current.velocity,now,500,direction,true);
    }else if(settling)previous.settling=true;
    continue;
   }
   // Model time is whole seconds: at 1x, 2 Hz heartbeats contain a changed
   // position only every second. Retain that full cadence instead of stopping
   // halfway through each observed second. Missing packets settle by 1200 ms.
   const heartbeat=clamp(snapshot.update_interval_ms||500,16,1000);
   const quantizedCadence=Number.isFinite(snapshot.speed)&&snapshot.speed>0?
    Math.ceil(1000/snapshot.speed/heartbeat)*heartbeat:0;
   const duration=settling?Math.min(500,this.cadence):clamp(Math.max(this.cadence,quantizedCadence)*1.15,250,1200);
   this.retarget(train.id,current.position,target,current.velocity,now,duration,direction,settling);
  }
 }

 private retarget(id:string,from:number,target:number,velocity:number,now:number,duration:number,direction:number,settling:boolean){
  const delta=target-from;
  const slope=Math.abs(delta)>EPSILON?clamp(velocity*duration/delta,0,3):0;
  this.segments.set(id,{from,target,start:now,end:Math.abs(delta)>EPSILON?now+duration:now,slope,direction,settling});
 }

 sample(trainId:string,now:number):TrainMotionSample|undefined {
  const segment=this.segments.get(trainId);
  if(!segment)return undefined;
  const {position,animating}=evaluate(segment,Number.isFinite(now)?now:this.lastPushAt);
  return {position,animating};
 }

 isAnimating(now:number):boolean {
  for(const segment of this.segments.values())if(evaluate(segment,now).animating)return true;
  return false;
 }
}
