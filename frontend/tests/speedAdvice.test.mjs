import {test} from 'node:test';
import assert from 'node:assert/strict';
import {currentProfile,currentProposal} from '../src/speedAdvice.ts';

const snapshot={epoch:'a',active_plan_id:'p',constraint_version:1,sim_time_s:100,running:false,replanning:false,awaiting_plan:false,dispatch:{valid:true}};
const profile={epoch:'a',plan_id:'p',train_id:'T05',constraint_version:1,provisional:false};
const proposal={epoch:'a',base_plan_id:'p',constraint_version:1,sim_time_s:100,plan:{eco:{train_id:'T05'}}};

test('profiles cannot cross train, reset, plan, or incident boundaries',()=>{
 assert.equal(currentProfile(profile,snapshot,'T05'),profile);
 for(const [state,id] of [[snapshot,'T06'],[{...snapshot,epoch:'b'},'T05'],[{...snapshot,active_plan_id:'q'},'T05'],[{...snapshot,constraint_version:2},'T05'],[{...snapshot,awaiting_plan:true},'T05'],[{...snapshot,dispatch:{valid:false}},'T05']]){
  assert.equal(currentProfile(profile,state,id),null);
 }
 const held={...profile,provisional:true};
 assert.equal(currentProfile(held,{...snapshot,awaiting_plan:true},'T05'),held);
});
test('economy proposals become unusable when time or operating conditions change',()=>{
 assert.equal(currentProposal(proposal,snapshot,'T05'),true);
 for(const changes of [{epoch:'b'},{active_plan_id:'q'},{constraint_version:2},{sim_time_s:101},{running:true},{awaiting_plan:true},{replanning:true}]){
  assert.equal(currentProposal(proposal,{...snapshot,...changes},'T05'),false);
 }
 assert.equal(currentProposal(proposal,snapshot,'T06'),false);
 assert.equal(currentProposal(null,snapshot,'T05'),false);
});
