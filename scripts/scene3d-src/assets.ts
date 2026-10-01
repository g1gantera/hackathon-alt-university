import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

/** Meter-scale, Y-up models. Static parts are merged per material and reused. */
const palette = {
  ivory: 0xf2f4ed, teal: 0x237b70, mint: 0x92c3b0, amber: 0xd1a052,
  charcoal: 0x29434a, glass: 0x537983, steel: 0x7c9292, concrete: 0xc3cabe,
  roof: 0x496c69, wood: 0x826f51, green: 0x75a885,
} as const;
type Finish = keyof typeof palette;
const materials = new Map<Finish, THREE.MeshStandardMaterial>();
const primitives = new Map<string, THREE.BufferGeometry>();
const templates = new Map<string, THREE.Group>();
const transform = new THREE.Matrix4();
const quaternion = new THREE.Quaternion();
const position = new THREE.Vector3();
const scale = new THREE.Vector3();
const euler = new THREE.Euler();

function material(finish: Finish) {
  let result = materials.get(finish);
  if (!result) {
    result = new THREE.MeshStandardMaterial({
      color: palette[finish], roughness: finish === 'glass' ? 0.35 : 0.86,
      metalness: finish === 'steel' ? 0.25 : 0.05, flatShading: true,
    });
    result.userData.sharedRailAsset = true;
    materials.set(finish, result);
  }
  return result;
}

function primitive(name: 'box' | 'wheel' | 'cone' | 'trunk' | 'roof' | 'carbody') {
  let result = primitives.get(name);
  if (result) return result;
  if (name === 'box') result = new THREE.BoxGeometry(1, 1, 1);
  else if (name === 'wheel') result = new THREE.CylinderGeometry(1, 1, 1, 8);
  else if (name === 'cone') result = new THREE.ConeGeometry(1, 1, 6);
  else if (name === 'trunk') result = new THREE.CylinderGeometry(0.8, 1, 1, 5);
  else {
    // Polygon extruded down the Z axis: a gabled roof or clipped carriage roof.
    const profile = name === 'roof'
      ? [[-0.5, 0], [0.5, 0], [0, 1]]
      : [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.27], [0.32, 0.5], [-0.32, 0.5], [-0.5, 0.27]];
    const shape = new THREE.Shape();
    profile.forEach(([x, y], index) => index ? shape.lineTo(x, y) : shape.moveTo(x, y));
    shape.closePath();
    result = new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false, steps: 1, curveSegments: 1 });
    result.translate(0, 0, -0.5);
  }
  // All parts use non-indexed position + normal data, giving deterministic merges.
  if (result.index) {
    const indexed = result;
    result = indexed.toNonIndexed();
    indexed.dispose();
  }
  result.deleteAttribute('uv');
  result.clearGroups();
  primitives.set(name, result);
  return result;
}

class Assembly {
  private batches = new Map<Finish, THREE.BufferGeometry[]>();
  add(shape: Parameters<typeof primitive>[0], finish: Finish,
    x: number, y: number, z: number, w: number, h: number, d: number,
    rx = 0, ry = 0, rz = 0) {
    const geometry = primitive(shape).clone();
    quaternion.setFromEuler(euler.set(rx, ry, rz));
    transform.compose(position.set(x, y, z), quaternion, scale.set(w, h, d));
    geometry.applyMatrix4(transform);
    const batch = this.batches.get(finish) ?? [];
    batch.push(geometry);
    this.batches.set(finish, batch);
    return this;
  }
  box(finish: Finish, x: number, y: number, z: number, w: number, h: number, d: number,
    rx = 0, ry = 0, rz = 0) {
    return this.add('box', finish, x, y, z, w, h, d, rx, ry, rz);
  }
  finish(name: string) {
    const group = new THREE.Group();
    group.name = name;
    this.batches.forEach((parts, finish) => {
      const geometry = mergeGeometries(parts, false);
      parts.forEach(part => part.dispose());
      if (!geometry) throw new Error(`Unable to merge rail asset ${name}/${finish}`);
      geometry.computeBoundingSphere();
      geometry.userData.sharedRailAsset = true;
      const mesh = new THREE.Mesh(geometry, material(finish));
      mesh.name = `${name}-${finish}`;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
    });
    return group;
  }
}

function cached(key: string, build: () => THREE.Group) {
  let template = templates.get(key);
  if (!template) {
    template = build();
    templates.set(key, template);
  }
  return template.clone(true);
}

function undercarriage(a: Assembly, center: number, length: number, includeBogies = true) {
  a.box('charcoal', 0, 0.95, center, 2.7, 0.45, length - 0.8);
  for (const z of includeBogies ? [-length * 0.32, length * 0.32] : []) {
    a.box('steel', 0, 0.66, center + z, 2.4, 0.45, 2.55);
    for (const axle of [-0.83, 0.83]) {
      for (const x of [-0.76, 0.76]) {
        a.add('wheel', 'charcoal', x, 0.48, center + z + axle, 0.48, 0.25, 0.48, 0, 0, Math.PI / 2);
      }
    }
  }
  for (const end of [-1, 1]) {
    a.box('charcoal', 0, 0.87, center + end * (length / 2 + 0.55), 0.38, 0.26, 1.15);
  }
}

function locomotiveBody(a: Assembly, type: 'passenger' | 'freight', includeBogies = true) {
  const accent = type === 'passenger' ? 'teal' : 'amber';
  undercarriage(a, 0, 18, includeBogies);
  a.add('carbody', accent, 0, 2.6, 0, 3.5, 2.7, 17.5);
  a.box('ivory', 0, 1.65, 0, 3.56, 0.45, 17.55);
  a.add('carbody', 'ivory', 0, 3.06, 6.9, 3.4, 2.35, 3.3);
  a.box('glass', 0, 3.2, 8.59, 2.6, 0.92, 0.06);
  a.box('ivory', 0, 3.2, 8.64, 0.14, 1.02, 0.08);
  a.box('charcoal', 0, 0.92, 9, 3.05, 0.72, 0.3);
  a.box(accent, 0, 2.08, 8.62, 2.75, 0.42, 0.14);
  for (const side of [-1, 1]) {
    a.box('glass', side * 1.72, 3.15, 7, 0.06, 0.88, 1.85);
    a.box('ivory', side * 1.15, 2.27, 8.76, 0.43, 0.35, 0.13);
    for (let vent = 0; vent < 5; vent++) {
      a.box('charcoal', side * 1.765, 2.87, -5.5 + vent * 1.5, 0.05, 0.85, 0.65);
    }
  }
  a.box('steel', 0, 4.05, -1.7, 2.1, 0.4, 6.8);
  a.box('charcoal', 0, 4.43, -2.9, 0.75, 0.38, 0.85);
  a.box('ivory', 0, 3.88, 8.45, 0.65, 0.22, 0.17);
}

function carriageBody(a: Assembly, type: 'passenger' | 'freight', car: number, z = 0, includeBogies = true) {
  undercarriage(a, z, 22, includeBogies);
  if (type === 'passenger') {
    a.add('carbody', 'ivory', 0, 2.7, z, 3.45, 2.95, 21.6);
    a.box('teal', 0, 1.67, z, 3.5, 0.52, 21.65);
    a.box('steel', 0, 4.14, z, 2.1, 0.3, 14.6);
    for (const side of [-1, 1]) {
      for (let window = 0; window < 9; window++) {
        a.box('glass', side * 1.74, 3.1, z - 8 + window * 2, 0.04, 0.94, 1.35);
      }
      for (const end of [-1, 1]) {
        a.box('teal', side * 1.75, 2.54, z + end * 9.7, 0.05, 2, 1.08);
        a.box('glass', side * 1.79, 3.04, z + end * 9.7, 0.03, 0.72, 0.65);
      }
    }
    for (const end of [-1, 1]) a.box('charcoal', 0, 2.24, z + end * 10.95, 1.5, 2.2, 0.35);
  } else {
    const container = car % 3 === 1 ? 'teal' : car % 3 === 2 ? 'ivory' : 'amber';
    a.box('steel', 0, 1.23, z, 3.25, 0.22, 21.8);
    a.box(container, 0, 2.86, z, 3.13, 3, 20.3);
    for (const side of [-1, 1]) {
      for (let rib = 0; rib < 11; rib++) a.box(container, side * 1.6, 2.84, z - 9.5 + rib * 1.9, 0.11, 2.92, 0.18);
      a.box('ivory', side * 1.67, 2.7, z + 3.3, 0.03, 0.4, 2.3);
    }
    a.box('steel', 0, 2.8, z - 10.22, 0.1, 2.7, 0.06);
  }
}

function boundedCarCount(carCount: number) {
  return Math.max(0, Math.min(12, Math.round(Number.isFinite(carCount) ? carCount : 0)));
}

/** Locomotive origin; +Z faces forward, each coach trails by 23.4 m. */
export function createTrain(type: 'passenger' | 'freight', carCount = type === 'passenger' ? 3 : 4): THREE.Group {
  const count = boundedCarCount(carCount);
  return cached(`train-${type}-${count}`, () => {
    const a = new Assembly();
    locomotiveBody(a, type);
    for (let car = 0; car < count; car++) carriageBody(a, type, car, -21.2 - car * 23.4);
    const result = a.finish(`${type}-train`);
    const bounds = new THREE.Box3().setFromObject(result);
    result.userData = {
      asset: 'train', type, carCount: count,
      frontOffset: bounds.max.z, rearOffset: -bounds.min.z,
      length: bounds.max.z - bounds.min.z,
      width: bounds.max.x - bounds.min.x, height: bounds.max.y,
    };
    return result;
  });
}

export interface BogieDescriptor {
  object: THREE.Group;
  /** Signed distance from the vehicle center in local +Z (front) meters. */
  offset: number;
}

export interface VehicleDescriptor {
  object: THREE.Group;
  /** Positive distance behind the locomotive center along the route. */
  centerOffset: number;
  /** Separation of this vehicle's two bogie pivots, in meters. */
  wheelbase: number;
  frontOffset: number;
  rearOffset: number;
  bogies: [BogieDescriptor, BogieDescriptor];
}

export interface RailConsist extends THREE.Group {
  userData: THREE.Group['userData'] & {
    asset: 'train';
    articulated: true;
    type: 'passenger' | 'freight';
    carCount: number;
    vehicles: VehicleDescriptor[];
    frontOffset: number;
    rearOffset: number;
    length: number;
    width: number;
    height: number;
  };
}

function createBogie() {
  return cached('articulated-bogie', () => {
    const a = new Assembly();
    // A single material batch keeps every independently steering bogie to one draw.
    a.box('charcoal', 0, 0.66, 0, 2.4, 0.45, 2.55);
    for (const axle of [-0.83, 0.83]) {
      for (const x of [-0.76, 0.76]) {
        a.add('wheel', 'charcoal', x, 0.48, axle, 0.48, 0.25, 0.48, 0, 0, Math.PI / 2);
      }
    }
    return a.finish('steering-bogie');
  });
}

/**
 * Independently placeable bodies and steering bogies, with the same straight-track
 * dimensions as createTrain. The root starts at the locomotive center, facing +Z.
 * Vehicle transforms are relative to the consist; bogie transforms are relative
 * to their vehicle. Construct another instance with this factory, not Group.clone:
 * the live descriptors intentionally reference this instance's child objects.
 */
export function createArticulatedTrain(type: 'passenger' | 'freight', carCount = type === 'passenger' ? 3 : 4): RailConsist {
  const count = boundedCarCount(carCount);
  const result = new THREE.Group() as RailConsist;
  result.name = `${type}-articulated-train`;
  const vehicles: VehicleDescriptor[] = [];
  for (let index = 0; index <= count; index++) {
    const locomotive = index === 0;
    const variant = type === 'freight' ? (index - 1) % 3 : 0;
    const key = locomotive ? `locomotive-body-${type}` : `carriage-body-${type}-${variant}`;
    const object = cached(key, () => {
      const a = new Assembly();
      if (locomotive) locomotiveBody(a, type, false);
      else carriageBody(a, type, variant, 0, false);
      return a.finish(locomotive ? `${type}-locomotive` : `${type}-carriage`);
    });
    const centerOffset = locomotive ? 0 : 21.2 + (index - 1) * 23.4;
    const wheelbase = (locomotive ? 18 : 22) * 0.64;
    const bogies = [wheelbase / 2, -wheelbase / 2].map(offset => {
      const bogie = createBogie();
      bogie.position.z = offset;
      object.add(bogie);
      return { object: bogie, offset };
    }) as [BogieDescriptor, BogieDescriptor];
    const bounds = new THREE.Box3().setFromObject(object);
    object.name = locomotive ? 'locomotive' : `${type === 'passenger' ? 'coach' : 'wagon'}-${index}`;
    object.userData = { asset: 'rail-vehicle', vehicleIndex: index, type, locomotive };
    object.position.z = -centerOffset;
    result.add(object);
    vehicles.push({ object, centerOffset, wheelbase, frontOffset: bounds.max.z, rearOffset: -bounds.min.z, bogies });
  }
  const bounds = new THREE.Box3().setFromObject(result);
  result.userData = {
    asset: 'train', articulated: true, type, carCount: count, vehicles,
    frontOffset: bounds.max.z, rearOffset: -bounds.min.z,
    length: bounds.max.z - bounds.min.z,
    width: bounds.max.x - bounds.min.x, height: bounds.max.y,
  };
  return result;
}

/** Track-facing platform edge X=-4.8; station extends toward positive X. */
export function createStation(kind: 'terminal' | 'station' | 'halt'): THREE.Group {
  return cached(`station-${kind}`, () => {
    const a = new Assembly();
    const length = kind === 'terminal' ? 130 : kind === 'station' ? 76 : 42;
    const roofLength = kind === 'terminal' ? 100 : kind === 'station' ? 52 : 24;
    a.box('concrete', 0, 0.35, 0, 9.6, 0.7, length);
    a.box('ivory', -4.73, 0.44, 0, 0.15, 0.66, length);
    a.box('amber', -3.98, 0.73, 0, 0.38, 0.06, length - 1);
    a.box('roof', 0.3, 5.15, 0, 7.5, 0.25, roofLength);
    a.box('ivory', -3.48, 5.05, 0, 0.12, 0.48, roofLength);
    const supports = kind === 'terminal' ? 8 : kind === 'station' ? 5 : 3;
    for (let i = 0; i < supports; i++) {
      const z = (i / (supports - 1) - 0.5) * (roofLength - 5);
      a.box('steel', 2.8, 2.95, z, 0.23, 4.5, 0.23);
      a.box('steel', 0.1, 4.89, z, 6.35, 0.17, 0.18);
      a.box('wood', 1.5, 1.2, z, 1, 0.18, 2.8);
      a.box('wood', 1.96, 1.58, z, 0.14, 0.8, 2.8);
      for (const benchEnd of [-1, 1]) a.box('steel', 1.5, 0.94, z + benchEnd, 0.6, 0.46, 0.15);
    }
    for (const z of [-length * 0.37, length * 0.37]) {
      a.box('steel', -2.5, 2.4, z, 0.14, 3.4, 0.14);
      a.box('teal', -2.5, 3.77, z, 0.18, 0.88, 3.6);
      a.box('ivory', -2.61, 3.77, z, 0.03, 0.12, 2.5);
    }
    if (kind === 'halt') {
      a.box('glass', 3.62, 2.58, 0, 0.14, 3.55, 15);
      a.box('ivory', 3.71, 1.23, 0, 0.12, 0.8, 15);
      a.box('teal', 3, 1.45, 10, 0.75, 1.5, 0.75);
    } else {
      const terminal = kind === 'terminal';
      const buildingLength = terminal ? 57 : 27;
      const buildingHeight = terminal ? 10 : 6.3;
      const buildingWidth = terminal ? 17 : 10;
      const center = 4.5 + buildingWidth / 2;
      a.box('ivory', center, buildingHeight / 2, 0, buildingWidth, buildingHeight, buildingLength);
      a.box('concrete', center, 0.42, 0, buildingWidth + 0.6, 0.84, buildingLength + 0.6);
      a.add('roof', 'roof', center, buildingHeight, 0, buildingWidth + 1.1, terminal ? 3.7 : 2.2, buildingLength + 1.1);
      a.box('teal', 4.4, 2.28, 0, 0.2, 3.75, 4.5);
      a.box('glass', 4.26, 2.34, 0, 0.12, 2.6, 3.7);
      const bays = terminal ? 10 : 5;
      for (let bay = 0; bay < bays; bay++) {
        const z = (bay - (bays - 1) / 2) * (buildingLength / bays);
        for (let floor = 0; floor < (terminal ? 2 : 1); floor++) {
          if (floor === 0 && Math.abs(z) < 3) continue;
          for (const x of [4.43, center + buildingWidth / 2 + 0.06]) {
            a.box('glass', x, 3.1 + floor * 4.1, z, 0.13, 1.8, 2.25);
            a.box('teal', x, 2.1 + floor * 4.1, z, 0.21, 0.17, 2.6);
          }
        }
      }
      a.box('teal', 4.3, buildingHeight - 0.8, 0, 0.2, 0.75, 10);
      a.box('ivory', 4.17, buildingHeight - 0.8, 0, 0.06, 0.14, 7.5);
      if (terminal) {
        a.box('ivory', center, 12.3, 0, 7, 5.6, 9);
        a.add('roof', 'teal', center, 15.1, 0, 8.3, 2.4, 10);
        a.box('glass', center - 3.57, 12.25, 0, 0.1, 2.1, 3);
        a.box('amber', center - 3.66, 12.25, 0, 0.12, 0.14, 1.9);
        a.box('amber', center - 3.66, 12.7, 0, 0.12, 1.03, 0.14);
      }
    }
    const result = a.finish(`${kind}-station`);
    result.userData = { asset: 'station', kind, platformLength: length, trackEdgeX: -4.8, length };
    return result;
  });
}

/** Lamp material is unique so signal aspects can be changed independently. */
export function createSignal(): THREE.Group {
  const result = cached('signal', () => {
    const a = new Assembly();
    a.box('concrete', 0, 0.3, 0, 1.15, 0.6, 1.15);
    a.box('steel', 0, 3.65, 0, 0.23, 6.5, 0.23);
    a.box('charcoal', 0, 6.4, 0, 0.94, 2.45, 0.47);
    a.box('charcoal', 0, 7.68, 0.12, 1.18, 0.18, 0.95);
    a.box('ivory', 0.42, 3.2, -0.06, 0.09, 5.5, 0.09);
    for (let rung = 0; rung < 12; rung++) a.box('steel', 0.2, 0.9 + rung * 0.45, -0.12, 0.45, 0.07, 0.1);
    const group = a.finish('rail-signal');
    group.userData.asset = 'signal';
    return group;
  });
  const lampMaterial = new THREE.MeshStandardMaterial({ color: 0x57b68c, emissive: 0x2f8d64, emissiveIntensity: 0.6, roughness: 0.55 });
  lampMaterial.userData.ownedRailAsset = true;
  const lamp = new THREE.Mesh(primitive('wheel'), lampMaterial);
  lamp.name = 'lamp';
  lamp.rotation.x = Math.PI / 2;
  lamp.scale.set(0.29, 0.14, 0.29);
  lamp.position.set(0, 6.5, 0.3);
  result.add(lamp);
  return result;
}

export function createSwitchMarker(): THREE.Group {
  return cached('switch', () => {
    const a = new Assembly();
    a.box('concrete', 0, 0.12, 0, 1.8, 0.24, 1.2);
    a.box('charcoal', 0, 0.43, 0, 1.25, 0.47, 0.85);
    a.box('steel', 0, 0.94, 0, 0.13, 0.8, 0.13);
    a.box('amber', 0, 1.48, 0, 1.15, 0.75, 0.17);
    a.box('ivory', 0, 1.47, 0.11, 0.77, 0.12, 0.08, 0, 0, -0.55);
    const result = a.finish('switch-machine');
    result.userData.asset = 'switch';
    return result;
  });
}

/** Three road maintenance shed; front doors face +Z. */
export function createDepot(): THREE.Group {
  return cached('depot', () => {
    const a = new Assembly();
    a.box('concrete', 0, 0.25, 0, 26, 0.5, 46);
    a.box('ivory', 0, 5.8, 0, 25, 11.1, 44);
    a.add('roof', 'roof', 0, 11.35, 0, 27, 4.2, 46);
    for (const x of [-8, 0, 8]) {
      a.box('charcoal', x, 3.8, 22.08, 5.5, 7, 0.16);
      a.box('teal', x, 8.45, 22.14, 5.75, 1.4, 0.22);
      a.box('glass', x, 9.73, 22.17, 4.1, 0.9, 0.24);
    }
    for (const side of [-1, 1]) {
      for (let z = -17; z < 20; z += 7) {
        a.box('teal', side * 12.55, 5.7, z, 0.25, 10.8, 0.28);
        a.box('glass', side * 12.62, 7.8, z + 2.9, 0.1, 1.5, 3.5);
      }
    }
    const result = a.finish('maintenance-depot');
    result.userData = { asset: 'depot', width: 27, length: 46 };
    return result;
  });
}

export function createTree(): THREE.Group {
  return cached('tree', () => {
    const a = new Assembly();
    a.add('trunk', 'wood', 0, 1.6, 0, 0.28, 3.2, 0.28);
    a.add('cone', 'green', 0, 4.4, 0, 2.3, 5.4, 2.3);
    a.add('cone', 'mint', 0, 6.2, 0, 1.65, 3.6, 1.65);
    return a.finish('low-poly-tree');
  });
}

/** Release instance-owned signal materials; shared geometry remains cached. */
export function disposeObject(object: THREE.Object3D): void {
  object.traverse(child => {
    if (!(child instanceof THREE.Mesh)) return;
    const list = Array.isArray(child.material) ? child.material : [child.material];
    list.forEach(item => { if (item.userData.ownedRailAsset) item.dispose(); });
  });
  object.removeFromParent();
}

export function objectStats(object: THREE.Object3D) {
  let meshes = 0;
  let triangles = 0;
  const geometries = new Set<THREE.BufferGeometry>();
  object.traverse(child => {
    if (!(child instanceof THREE.Mesh)) return;
    meshes++;
    geometries.add(child.geometry);
    const vertices = child.geometry.index?.count ?? child.geometry.getAttribute('position').count;
    triangles += vertices / 3 * (child instanceof THREE.InstancedMesh ? child.count : 1);
  });
  return { meshes, triangles, geometries: geometries.size };
}
