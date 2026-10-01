import {test} from 'node:test';
import assert from 'node:assert/strict';
import {comparisonRows,comparisonValue,comparisonResponseMatches,sameQualityFormula} from '../src/replanComparison.ts';

const before={index:95,total_delay_s:0,max_delay_s:0,energy_kwh:1000,conflicts:3,quality_signature:'q1',forecast_valid:false,terminal_arrivals:{on_time_pct:100}};
const after={index:88,total_delay_s:600,max_delay_s:120,energy_kwh:980,conflicts:0,quality_signature:'q1',forecast_valid:true,terminal_arrivals:{on_time_pct:75}};
const comparison={old_plan_id:'old',new_plan_id:'chosen',before,after};

test('comparison shows actual signed costs even when fixing conflicts lowers quality',()=>{
 const rows=comparisonRows(comparison);
 assert.deepEqual(rows.map(row=>[row.key,row.delta]),[['index',-7],['conflicts',-3],['total_delay_s',600],['max_delay_s',120],['energy_kwh',-20],['terminal_accuracy',-25]]);
 assert.equal(rows.find(r=>r.key==='index').before,95);
 assert.equal(comparisonValue(rows.find(r=>r.key==='total_delay_s').delta,'seconds',true),'+10 мин');
 assert.equal(comparisonValue(-7,'score',true),'−7.0 балла');
});

test('missing values and incompatible formulas do not invent comparable improvements',()=>{
 const changed={...comparison,after:{...after,quality_signature:'q2',terminal_arrivals:{on_time_pct:null},energy_kwh:Infinity}};
 const rows=comparisonRows(changed);
 assert.equal(sameQualityFormula(changed),false);
 assert.equal(rows.find(r=>r.key==='index').delta,null);
 assert.equal(rows.find(r=>r.key==='index').after,88);
 assert.equal(rows.find(r=>r.key==='terminal_accuracy').delta,null);
 assert.equal(rows.find(r=>r.key==='energy_kwh').after,null);
 assert.equal(sameQualityFormula({before:{index:100},after:{index:90}}),false);
 assert.equal(comparisonValue(null,'score'),'—');
});

test('delta formatting preserves signs, small values and exact seconds',()=>{
 assert.equal(comparisonValue(61,'seconds',true),'+1 мин 1 с');
 assert.equal(comparisonValue(-1,'seconds',true),'−1 с');
 assert.equal(comparisonValue(0,'count',true),'0');
 assert.equal(comparisonValue(.01,'kwh',true),'+<0.1 кВт·ч');
 assert.equal(comparisonValue(-.01,'kwh',true),'−<0.1 кВт·ч');
 assert.equal(comparisonValue(-25,'percent',true),'−25.0 п.п.');
});

test('responses cannot cross candidate selections, resets, active plans, formulas or incidents',()=>{
 const snapshot={epoch:'run',constraint_version:2,active_plan_id:'old',sim_time_s:100,metrics:{quality_signature:'q1'}};
 const data={epoch:'run',constraint_version:2,active_plan_id:'old',plan_id:'chosen',sim_time_s:90,comparison};
 assert.equal(comparisonResponseMatches(data,snapshot,'chosen'),true);
 assert.equal(comparisonResponseMatches(data,snapshot,'other'),false);
 for(const change of [{epoch:'new-run'},{constraint_version:3},{active_plan_id:'new-active'},{sim_time_s:80},{metrics:{quality_signature:'q2'}}])assert.equal(comparisonResponseMatches(data,{...snapshot,...change},'chosen'),false);
 assert.equal(comparisonResponseMatches({...data,comparison:{...comparison,new_plan_id:'wrong'}},snapshot,'chosen'),false);
 assert.equal(comparisonResponseMatches(null,snapshot,'chosen'),false);
});
