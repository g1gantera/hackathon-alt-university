import assert from 'node:assert/strict';
import test from 'node:test';
import {trackCategory,trackCategories} from '../static/track-colors.mjs';
test('OSM service overrides main usage; transit categories stay separate',()=>{
  assert.equal(trackCategory({railway:'rail',usage:'main',service:'siding'}),'siding');
  assert.equal(trackCategory({railway:'rail',usage:'main',service:'crossover'}),'crossover');
  assert.equal(trackCategory({railway:'tram',service:'yard'}),'tram');
  assert.equal(trackCategory({railway:'rail',usage:'industrial'}),'industrial');
  assert.equal(trackCategory({railway:'rail'}),'line');
  assert.equal(new Set(trackCategories.map(c=>c[2])).size,12);
});
