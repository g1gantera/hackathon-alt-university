import {test} from 'node:test';
import assert from 'node:assert/strict';
import {qualityStatus} from '../src/quality.ts';
import {settingsError,weightPercentages} from '../src/qualitySettings.ts';

const settings={passenger_weight:3,freight_weight:1,delay_weight:.7,energy_weight:.3,delay_norm_s:7200,energy_norm_kwh:100000,arrival_tolerance_s:300,quality_weights:{schedule:42,energy:18,capacity:20,conflicts:10,arrival_accuracy:10},quality_formula:'weighted_mean',quality_threshold_normal:90,quality_threshold_attention:70,conflict_penalty:1};

test('quality categories use the saved assessment and thresholds, including legacy snapshots',()=>{
 assert.equal(qualityStatus({index:96,assessment:'disrupted'}).label,'Критично');
 const formula={thresholds:{normal:85,attention:75}};
 assert.equal(qualityStatus({index:85,formula}).label,'Норма');
 assert.equal(qualityStatus({index:75,formula}).label,'Внимание');
 assert.equal(qualityStatus({index:74.9,formula}).tone,'poor');
 assert.equal(qualityStatus({index:89.9}).label,'Внимание');
 assert.equal(qualityStatus({index:69.9}).label,'Критично');
});

test('relative weights show normalized shares including excluded components',()=>{
 assert.deepEqual(weightPercentages(settings.quality_weights),settings.quality_weights);
 const weights={schedule:2,capacity:6,energy:0,conflicts:0,arrival_accuracy:0};
 assert.deepEqual(weightPercentages(weights),{...weights,schedule:25,capacity:75});
 assert.deepEqual(weightPercentages({...weights,schedule:0,capacity:0}),{schedule:0,capacity:0,energy:0,conflicts:0,arrival_accuracy:0});
});

test('invalid local inputs prevent saving',()=>{
 assert.equal(settingsError(settings),null);
 for(const change of [
  {quality_weights:{schedule:0,energy:0,capacity:0,conflicts:0,arrival_accuracy:0}},
  ...[-1,1001,NaN,Infinity].map(capacity=>({quality_weights:{...settings.quality_weights,capacity}})),
  {quality_threshold_normal:70},{quality_threshold_attention:90},{quality_threshold_attention:-1},
  {quality_threshold_normal:101},{quality_threshold_normal:NaN},{conflict_penalty:0},{arrival_tolerance_s:3601},
 ])assert.equal(typeof settingsError({...settings,...change}),'string');
 assert.equal(settingsError({...settings,quality_weights:{schedule:0,energy:0,capacity:5,conflicts:0,arrival_accuracy:0}}),null);
});
