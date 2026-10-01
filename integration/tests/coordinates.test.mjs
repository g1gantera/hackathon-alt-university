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
