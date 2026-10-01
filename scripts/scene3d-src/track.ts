import * as THREE from 'three';

export interface TrackDetailPath {
  points: [number, number][];
  category: string;
}
interface Segment {
  x: number; z: number; dx: number; dz: number;
  length: number; start: number; angle: number; category: string;
}
interface ClippedSegment { source: Segment; from: number; to: number }
const MAX_SLEEPERS = 30_000;
const MAX_SEGMENTS = 12_000;
const BASE_SPACING = 0.65;
const EPSILON = 1e-7;
const indexCache = new WeakMap<TrackDetailPath[], Segment[]>();
const box = new THREE.BoxGeometry(1, 1, 1);
box.userData.sharedRailAsset = true;
const ballastMaterial = new THREE.MeshLambertMaterial({ color: 0xb6b4a4, flatShading: true });
const sleeperMaterial = new THREE.MeshLambertMaterial({ color: 0x796f60, flatShading: true });
const railMaterial = new THREE.MeshLambertMaterial({ color: 0x525d59, flatShading: true });
for (const material of [ballastMaterial, sleeperMaterial, railMaterial]) material.userData.sharedRailAsset = true;

function index(paths: TrackDetailPath[]) {
  const cached = indexCache.get(paths);
  if (cached) return cached;
  const segments: Segment[] = [];
  for (const path of paths) {
    let start = 0;
    for (let i = 1; i < path.points.length; i++) {
      const a = path.points[i - 1], b = path.points[i];
      if (![a[0], a[1], b[0], b[1]].every(Number.isFinite)) continue;
      const vx = b[0] - a[0], vz = b[1] - a[1];
      const length = Math.hypot(vx, vz);
      if (length > EPSILON) {
        segments.push({ x: a[0], z: a[1], dx: vx / length, dz: vz / length,
          length, start, angle: Math.atan2(vx, vz), category: path.category });
      }
      start += length;
    }
  }
  indexCache.set(paths, segments);
  return segments;
}

/** Exact intersection of a finite centerline segment and the detail circle. */
function clip(segment: Segment, x: number, z: number, radiusSquared: number): ClippedSegment | undefined {
  const px = x - segment.x, pz = z - segment.z;
  const along = px * segment.dx + pz * segment.dz;
  const perpendicular = px * segment.dz - pz * segment.dx;
  const remaining = radiusSquared - perpendicular * perpendicular;
  if (remaining <= 0) return undefined;
  const half = Math.sqrt(remaining);
  const from = Math.max(0, along - half), to = Math.min(segment.length, along + half);
  return to - from > EPSILON ? { source: segment, from, to } : undefined;
}

function firstSleeper(segment: ClippedSegment, spacing: number) {
  // A path-global grid, not the clipping boundary: panning never relocates ties.
  return Math.ceil((segment.source.start + segment.from - EPSILON) / spacing);
}

/**
 * Physical rail details over unchanged world-meter [x,z] paths. Geometry and
 * materials are shared; call result.userData.dispose() to release only this
 * group's GPU instance buffers when replacing it. No simulation state is read.
 */
export function buildTrackDetail(paths: TrackDetailPath[], center: [number, number], radius: number): THREE.Group {
  const group = new THREE.Group();
  group.name = 'physical-track-detail';
  const meshes: THREE.InstancedMesh[] = [];
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const mesh of meshes) mesh.dispose();
    group.clear();
    group.removeFromParent();
  };
  group.userData = { category: 'track-detail', dispose, sleeperCount: 0, segmentCount: 0, railCount: 0 };
  if (!Number.isFinite(radius) || radius <= 0 || !center.every(Number.isFinite)) return group;
  // Keep Float32 instance translations local. The renderer subtracts the camera
  // from this group's double-precision world matrix before uploading uniforms.
  group.position.set(center[0], 0, center[1]);
  const detailRadius = Math.min(radius, 1500);
  // Wider views use a subset of the same .65 m lattice, avoiding sliding ties.
  const spacing = BASE_SPACING * (detailRadius <= 650 ? 1 : detailRadius <= 1100 ? 2 : 4);
  const clipped: ClippedSegment[] = [];
  let segmentLimit = false;
  for (const segment of index(paths)) {
    const visible = clip(segment, center[0], center[1], detailRadius * detailRadius);
    if (!visible) continue;
    if (clipped.length >= MAX_SEGMENTS) { segmentLimit = true; break; }
    clipped.push(visible);
  }
  let sleeperCount = 0;
  for (const segment of clipped) {
    const end = segment.source.start + segment.to;
    const first = firstSleeper(segment, spacing);
    // Excluding segment ends avoids duplicate sleepers at shared polyline vertices.
    sleeperCount += Math.max(0, Math.ceil((end - EPSILON) / spacing) - first);
  }
  const candidateSleepers = sleeperCount;
  sleeperCount = Math.min(MAX_SLEEPERS, sleeperCount);
  const add = (name: string, material: THREE.Material, count: number) => {
    if (!count) return undefined;
    const mesh = new THREE.InstancedMesh(box, material, count);
    mesh.name = name;
    mesh.userData.category = 'track-detail';
    mesh.receiveShadow = true;
    group.add(mesh);
    meshes.push(mesh);
    return mesh;
  };
  const ballast = add('track-ballast', ballastMaterial, clipped.length);
  const rails = add('track-rails', railMaterial, clipped.length * 2);
  const sleepers = add('track-sleepers', sleeperMaterial, sleeperCount);
  const dummy = new THREE.Object3D();
  let nextSleeper = 0;
  const categories: Record<string, number> = {};
  for (let i = 0; i < clipped.length; i++) {
    const segment = clipped[i], source = segment.source;
    const middle = (segment.from + segment.to) / 2, length = segment.to - segment.from;
    const x = source.x + source.dx * middle, z = source.z + source.dz * middle;
    dummy.rotation.set(0, source.angle, 0);
    dummy.position.set(x - center[0], -0.09, z - center[1]);
    dummy.scale.set(4.2, 0.16, length);
    dummy.updateMatrix();
    ballast!.setMatrixAt(i, dummy.matrix);
    for (let side = 0; side < 2; side++) {
      const offset = side ? 0.76 : -0.76;
      dummy.position.set(x - center[0] + source.dz * offset, 0.28, z - center[1] - source.dx * offset);
      dummy.scale.set(0.15, 0.22, length);
      dummy.updateMatrix();
      rails!.setMatrixAt(i * 2 + side, dummy.matrix);
    }
    const lastDistance = source.start + segment.to;
    for (let lattice = firstSleeper(segment, spacing); lattice * spacing < lastDistance - EPSILON && nextSleeper < sleeperCount; lattice++) {
      const distance = lattice * spacing - source.start;
      dummy.position.set(source.x + source.dx * distance - center[0], 0.07, source.z + source.dz * distance - center[1]);
      dummy.scale.set(2.8, 0.16, 0.28);
      dummy.updateMatrix();
      sleepers!.setMatrixAt(nextSleeper++, dummy.matrix);
    }
    categories[source.category] = (categories[source.category] ?? 0) + 1;
  }
  for (const mesh of meshes) {
    // count is exact, including an empty/tangent window, so no identity ghosts.
    if (mesh === sleepers) mesh.count = nextSleeper;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingBox();
    mesh.computeBoundingSphere();
  }
  Object.assign(group.userData, {
    radius: detailRadius, origin: [...center], sleeperSpacing: spacing, sleeperCount: nextSleeper,
    segmentCount: clipped.length, railCount: clipped.length * 2, categories,
    truncated: segmentLimit || candidateSleepers > MAX_SLEEPERS,
  });
  return group;
}
