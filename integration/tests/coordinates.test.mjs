import assert from 'node:assert/strict';
import test from 'node:test';
import { createLocator } from '../static/coordinates.mjs';

const topology = {
  length_m: 2000,
  stations: [
    { id: 'a', position_m: 0, coordinate: [70, 51] },
    { id: 'b', position_m: 1000, coordinate: [70.1, 51] },
    { id: 'c', position_m: 2000, coordinate: [70.1, 51.1] },
  ],
  sections: [
    { id: 'ab', from_station: 'a', to_station: 'b', geometry: [[70, 51], [70.05, 51], [70.1, 51]] },
    { id: 'bc', from_station: 'b', to_station: 'c', geometry: [[70.1, 51], [70.1, 51.1]] },
  ],
};
const locate = createLocator(topology);

test('both termini and every intermediate station map to exact coordinates', () => {
  for (const station of topology.stations) {
    assert.deepEqual(locate({ position_m: station.position_m }), station.coordinate);
    assert.deepEqual(locate({ station_id: station.id }), station.coordinate);
  }
});

test('forward and reverse trains use the same physical corridor position', () => {
  for (const direction of [1, -1]) {
    const [lon, lat] = locate({ position_m: 1500, section_id: 'bc', direction });
    assert.equal(lon, 70.1);
    assert.ok(Math.abs(lat - 51.05) < 1e-8);
  }
});

test('position stays on section geometry and clamps at corridor ends', () => {
  assert.deepEqual(locate({ position_m: -1 }), [70, 51]);
  assert.deepEqual(locate({ position_m: 2500 }), [70.1, 51.1]);
  const [lon, lat] = locate({ position_m: 500 });
  assert.ok(Math.abs(lon - 70.05) < 1e-8);
  assert.equal(lat, 51);
});

test('opposing movements at the same kilometre use assigned running lines', () => {
  const model=structuredClone(topology);
  model.sections[0].main_tracks=[
    {id:'1',geometry:[[70,51.00003],[70.1,51.00003]]},
    {id:'2',geometry:[[70,50.99997],[70.1,50.99997]]},
  ];
  const at=createLocator(model);
  const forward=at({position_m:500,section_id:'ab',main_track_id:'1',direction:1});
  const reverse=at({position_m:500,section_id:'ab',main_track_id:'2',direction:-1});
  assert.notDeepEqual(forward,reverse);
  assert.equal(forward[1],51.00003);
  assert.equal(reverse[1],50.99997);
});

test('station reservations select different anchors; off-network trains have no coordinate', () => {
  const model=structuredClone(topology);
  model.stations[0].track_layout=[{id:'1',coordinate:[70,51.00003]},{id:'2',coordinate:[70,50.99997]}];
  const at=createLocator(model);
  assert.deepEqual(at({station_id:'a',station_track_id:'1'}),[70,51.00003]);
  assert.deepEqual(at({station_id:'a',station_track_id:'2'}),[70,50.99997]);
  for(const status of ['scheduled','queued','completed'])assert.equal(at({on_network:false,status,station_id:'a'}),null);
});

test('movement joins its reserved station track in both directions without an endpoint jump', () => {
  const model=structuredClone(topology);
  model.stations[0].track_layout=[{id:'platform',coordinate:[70,51.0001]}];
  const at=createLocator(model);
  for(const direction of [1,-1]){
    const train={position_m:0,section_id:'ab',direction,[direction===1?'departure_track_id':'arrival_track_id']:'platform'};
    assert.deepEqual(at(train),[70,51.0001]);
    assert.ok(Math.abs(at({...train,position_m:.01})[1]-51.0001)<1e-8);
  }
});

test('every throat position lies on the same explicit connector drawn on the map', async () => {
  const {movementGeometry,connectorFeatures}=await import('../static/coordinates.mjs');
  const model=structuredClone(topology);
  model.stations[0].track_layout=[{id:'side',coordinate:[70,51.001]}];
  model.stations[1].track_layout=[{id:'side',coordinate:[70.1,50.999]}];
  const knots=movementGeometry(model,model.sections[0],undefined,'side','side');
  const at=createLocator(model);
  for(const direction of [1,-1])for(let metre=0;metre<=1000;metre+=5){
    const p=at({position_m:metre,section_id:'ab',direction,departure_track_id:'side',arrival_track_id:'side'});
    const f=metre/1000,i=knots.findIndex(k=>k.fraction>=f);
    const a=knots[Math.max(0,i-1)].coordinate,b=knots[i].coordinate;
    const cross=(p[0]-a[0])*(b[1]-a[1])-(p[1]-a[1])*(b[0]-a[0]);
    assert.ok(Math.abs(cross)<1e-12);
  }
  const features=connectorFeatures(model).features;
  assert.deepEqual(features[0].geometry.coordinates,knots.slice(0,2).map(k=>k.coordinate));
  assert.deepEqual(at({position_m:1000,section_id:'ab',direction:1,arrival_track_id:'side'}),model.stations[1].track_layout[0].coordinate);
});
