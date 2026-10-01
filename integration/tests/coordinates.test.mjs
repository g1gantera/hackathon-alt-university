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
