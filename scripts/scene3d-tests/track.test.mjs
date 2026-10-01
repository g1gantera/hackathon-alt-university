import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Matrix4, Vector3, Quaternion, Group } from 'three';
import { buildTrackDetail } from '../scene3d-src/track.ts';

const near = (actual, expected, tolerance = 0.0001) => {
  assert.ok(Math.abs(actual - expected) < tolerance, `${actual} differs from ${expected}`);
};
function instances(mesh) {
  const result = [];
  if (!mesh) return result;
  mesh.updateWorldMatrix(true, false);
  for (let i = 0; i < mesh.count; i++) {
    const matrix = new Matrix4(), position = new Vector3(), rotation = new Quaternion(), scale = new Vector3();
    mesh.getMatrixAt(i, matrix);
    matrix.premultiply(mesh.matrixWorld);
    matrix.decompose(position, rotation, scale);
    result.push({ position, rotation, scale });
  }
  return result;
}
const path = points => ({ points, category: 'main' });

test('finite segments are clipped to the exact circular detail window', () => {
  const detail = buildTrackDetail([path([[-100, 6], [100, 6]])], [0, 0], 10);
  const ballast = instances(detail.getObjectByName('track-ballast'))[0];
  near(ballast.position.x, 0); near(ballast.position.z, 6);
  near(ballast.scale.z, 16); // x = ±sqrt(10² - 6²), not a square viewport.
  assert.equal(detail.userData.segmentCount, 1);
  const short = buildTrackDetail([path([[2, 0], [5, 0]])], [0, 0], 10);
  const bounded = instances(short.getObjectByName('track-ballast'))[0];
  near(bounded.position.x, 3.5); near(bounded.scale.z, 3);
  const tangent = buildTrackDetail([path([[-100, 10], [100, 10]])], [0, 0], 10);
  assert.equal(tangent.children.length, 0);
  detail.userData.dispose(); short.userData.dispose(); tangent.userData.dispose();
});

test('physical rails maintain 1.52m gauge and rolling-stock contact height', () => {
  for (const points of [[[-100, 0], [100, 0]], [[0, -100], [0, 100]], [[-100, -100], [100, 100]]]) {
    const detail = buildTrackDetail([path(points)], [0, 0], 10);
    const rails = instances(detail.getObjectByName('track-rails'));
    assert.equal(rails.length, 2);
    near(rails[0].position.distanceTo(rails[1].position), 1.52);
    near(rails[0].position.y + rails[0].scale.y / 2, 0.39);
    near(rails[0].scale.x, 0.15);
    assert.equal(detail.children.length, 3);
    detail.userData.dispose();
  }
});

test('panning retains the same cumulative-distance sleeper positions across polyline vertices', () => {
  const paths = [path([[-100, 0], [0, 0], [100, 0]])];
  const before = buildTrackDetail(paths, [0, 0], 20);
  const after = buildTrackDetail(paths, [5, 0], 20);
  const positions = detail => instances(detail.getObjectByName('track-sleepers'))
    .map(instance => instance.position.x).filter(x => x > -10 && x < 10).map(x => x.toFixed(4));
  assert.deepEqual(positions(before), positions(after));
  const all = instances(before.getObjectByName('track-sleepers')).map(instance => instance.position.x.toFixed(4));
  assert.equal(new Set(all).size, all.length, 'A vertex must not produce duplicate sleepers');
  assert.equal(before.userData.sleeperSpacing, 0.65);
  before.userData.dispose(); after.userData.dispose();
});

test('wider LOD uses a subset of the same anchored sleeper lattice', () => {
  const paths = [path([[-2000, 0], [2000, 0]])];
  const close = buildTrackDetail(paths, [0, 0], 600);
  const medium = buildTrackDetail(paths, [0, 0], 1000);
  const wide = buildTrackDetail(paths, [0, 0], 1500);
  assert.deepEqual([close, medium, wide].map(detail => detail.userData.sleeperSpacing), [0.65, 1.3, 2.6]);
  const nearPositions = new Set(instances(close.getObjectByName('track-sleepers')).map(instance => instance.position.x.toFixed(3)));
  for (const detail of [medium, wide]) {
    for (const { position } of instances(detail.getObjectByName('track-sleepers'))) {
      if (Math.abs(position.x) < 590) assert.ok(nearPositions.has(position.x.toFixed(3)), 'LOD must thin ties without relocating them');
    }
  }
  close.userData.dispose(); medium.userData.dispose(); wide.userData.dispose();
});

test('dense windows cap sleeper and segment allocations while preserving three draw calls', () => {
  const dense = buildTrackDetail(Array.from({ length: 100 }, (_, z) => ({ points: [[-5000, z], [5000, z]], category: 'yard' })), [0, 0], 3000);
  assert.equal(dense.userData.radius, 1500);
  assert.equal(dense.userData.sleeperCount, 30_000);
  assert.equal(dense.getObjectByName('track-sleepers').count, 30_000);
  assert.equal(dense.userData.truncated, true);
  assert.equal(dense.children.length, 3);
  assert.equal(dense.userData.railCount, dense.userData.segmentCount * 2);
  const fragmented = buildTrackDetail(Array.from({ length: 12_010 }, () => path([[-1, 0], [1, 0]])), [0, 0], 10);
  assert.equal(fragmented.userData.segmentCount, 12_000);
  assert.equal(fragmented.userData.railCount, 24_000);
  assert.equal(fragmented.userData.truncated, true);
  assert.equal(fragmented.children.length, 3);
  dense.userData.dispose(); fragmented.userData.dispose();
});

test('replacement disposes instance buffers once while preserving shared geometry and materials', () => {
  const first = buildTrackDetail([path([[-100, 0], [100, 0]])], [0, 0], 10);
  const second = buildTrackDetail([path([[-100, 0], [100, 0]])], [0, 0], 10);
  const parent = new Group(); parent.add(first);
  let sharedDisposals = 0, instanceDisposals = 0;
  const shared = new Set(), onShared = () => { sharedDisposals++; };
  first.children.forEach((mesh, i) => {
    assert.equal(mesh.geometry, second.children[i].geometry);
    assert.equal(mesh.material, second.children[i].material);
    assert.equal(mesh.geometry.userData.sharedRailAsset, true);
    assert.equal(mesh.material.userData.sharedRailAsset, true);
    shared.add(mesh.geometry); shared.add(mesh.material);
    mesh.addEventListener('dispose', () => { instanceDisposals++; });
  });
  for (const resource of shared) resource.addEventListener('dispose', onShared);
  first.userData.dispose(); first.userData.dispose();
  assert.equal(sharedDisposals, 0); assert.equal(instanceDisposals, 3);
  assert.equal(first.parent, null); assert.equal(first.children.length, 0);
  assert.equal(second.children.length, 3);
  for (const resource of shared) resource.removeEventListener('dispose', onShared);
  second.userData.dispose();
});

test('large geographic coordinates use local instance transforms without losing the world gauge', () => {
  const center = [702341.123456, 400732.987654];
  const detail = buildTrackDetail([path([[center[0] - 100, center[1] + 6], [center[0] + 100, center[1] + 6]])], center, 10);
  near(detail.position.x, center[0]); near(detail.position.z, center[1]);
  for (const mesh of detail.children) {
    for (let i = 0; i < mesh.count; i++) {
      const matrix = new Matrix4(); mesh.getMatrixAt(i, matrix);
      assert.ok(Math.abs(matrix.elements[12]) < 12 && Math.abs(matrix.elements[14]) < 12,
        'GPU instance translations must stay near the local origin');
    }
  }
  const ballast = instances(detail.getObjectByName('track-ballast'))[0];
  near(ballast.position.x, center[0], 0.000001);
  near(ballast.position.z, center[1] + 6, 0.000001);
  const rails = instances(detail.getObjectByName('track-rails'));
  near(rails[0].position.distanceTo(rails[1].position), 1.52, 0.000001);
  detail.userData.dispose();
});

test('empty, invalid and distant windows contain no identity-transform ghost meshes', () => {
  const paths = [path([[0, 0], [100, 0]])];
  for (const radius of [0, -1, NaN, Infinity]) {
    const detail = buildTrackDetail(paths, [0, 0], radius);
    assert.equal(detail.children.length, 0); detail.userData.dispose();
  }
  for (const [source, center] of [[[], [0, 0]], [paths, [1e6, 1e6]], [paths, [NaN, 0]]]) {
    const detail = buildTrackDetail(source, center, 100);
    assert.equal(detail.children.length, 0); detail.userData.dispose();
  }
});
