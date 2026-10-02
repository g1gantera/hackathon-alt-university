import {translate, displayText} from '../i18n/core.ts';
import { VS, FS, THEMES, consistFor } from './materials';
// WebGL2 renderer for the 3D view: live trains from the snapshot on the real corridor geometry.
// Positions are kept relative to the camera target so float32 stays precise 300 km from the origin.
import type { Snapshot, Topology, Train } from '../types';
import type { MeshData } from './mesh';
import { createBoxcar, createCoach, createCoalWagon, createLocomotive, createShadow, createStation, VEHICLES, type VehicleKind } from './models';
import { buildRoute, buildTrackChunks, createGround, createMast, createSleeper, type Route, type Zone } from './track';
export type CameraMode = '2d' | '3d';
type Theme = 'light' | 'dark';
interface Instance {
    x: number;
    y: number;
    z: number;
    yaw: number;
}
const SKY_VS = `#version 300 es
const vec2 p[3]=vec2[3](vec2(-1,-1),vec2(3,-1),vec2(-1,3));out vec2 vNdc;
void main(){vNdc=p[gl_VertexID];gl_Position=vec4(p[gl_VertexID],.999,1.);}`;
const SKY_FS = `#version 300 es
precision highp float;in vec2 vNdc;uniform mat4 uInv;uniform vec3 uTop,uHorizon;out vec4 outColor;
void main(){vec4 a=uInv*vec4(vNdc,-1.,1.),b=uInv*vec4(vNdc,1.,1.);vec3 dir=normalize(b.xyz/b.w-a.xyz/a.w);
 vec3 c=mix(uHorizon,uTop,pow(clamp(dir.y,0.,1.),.55));outColor=vec4(pow(c,vec3(1./2.2)),1.);}`;
function mat4() { return new Float32Array(16); }
function multiply(a: Float32Array, b: Float32Array) {
    const o = mat4();
    for (let c = 0; c < 4; c++)
        for (let r = 0; r < 4; r++) {
            let s = 0;
            for (let k = 0; k < 4; k++)
                s += a[k * 4 + r] * b[c * 4 + k];
            o[c * 4 + r] = s;
        }
    return o;
}
function invert(m: Float32Array) {
    const inv = mat4(), a = m;
    inv[0] = a[5] * a[10] * a[15] - a[5] * a[11] * a[14] - a[9] * a[6] * a[15] + a[9] * a[7] * a[14] + a[13] * a[6] * a[11] - a[13] * a[7] * a[10];
    inv[4] = -a[4] * a[10] * a[15] + a[4] * a[11] * a[14] + a[8] * a[6] * a[15] - a[8] * a[7] * a[14] - a[12] * a[6] * a[11] + a[12] * a[7] * a[10];
    inv[8] = a[4] * a[9] * a[15] - a[4] * a[11] * a[13] - a[8] * a[5] * a[15] + a[8] * a[7] * a[13] + a[12] * a[5] * a[11] - a[12] * a[7] * a[9];
    inv[12] = -a[4] * a[9] * a[14] + a[4] * a[10] * a[13] + a[8] * a[5] * a[14] - a[8] * a[6] * a[13] - a[12] * a[5] * a[10] + a[12] * a[6] * a[9];
    inv[1] = -a[1] * a[10] * a[15] + a[1] * a[11] * a[14] + a[9] * a[2] * a[15] - a[9] * a[3] * a[14] - a[13] * a[2] * a[11] + a[13] * a[3] * a[10];
    inv[5] = a[0] * a[10] * a[15] - a[0] * a[11] * a[14] - a[8] * a[2] * a[15] + a[8] * a[3] * a[14] + a[12] * a[2] * a[11] - a[12] * a[3] * a[10];
    inv[9] = -a[0] * a[9] * a[15] + a[0] * a[11] * a[13] + a[8] * a[1] * a[15] - a[8] * a[3] * a[13] - a[12] * a[1] * a[11] + a[12] * a[3] * a[9];
    inv[13] = a[0] * a[9] * a[14] - a[0] * a[10] * a[13] - a[8] * a[1] * a[14] + a[8] * a[2] * a[13] + a[12] * a[1] * a[10] - a[12] * a[2] * a[9];
    inv[2] = a[1] * a[6] * a[15] - a[1] * a[7] * a[14] - a[5] * a[2] * a[15] + a[5] * a[3] * a[14] + a[13] * a[2] * a[7] - a[13] * a[3] * a[6];
    inv[6] = -a[0] * a[6] * a[15] + a[0] * a[7] * a[14] + a[4] * a[2] * a[15] - a[4] * a[3] * a[14] - a[12] * a[2] * a[7] + a[12] * a[3] * a[6];
    inv[10] = a[0] * a[5] * a[15] - a[0] * a[7] * a[13] - a[4] * a[1] * a[15] + a[4] * a[3] * a[13] + a[12] * a[1] * a[7] - a[12] * a[3] * a[5];
    inv[14] = -a[0] * a[5] * a[14] + a[0] * a[6] * a[13] + a[4] * a[1] * a[14] - a[4] * a[2] * a[13] - a[12] * a[1] * a[6] + a[12] * a[2] * a[5];
    inv[3] = -a[1] * a[6] * a[11] + a[1] * a[7] * a[10] + a[5] * a[2] * a[11] - a[5] * a[3] * a[10] - a[9] * a[2] * a[7] + a[9] * a[3] * a[6];
    inv[7] = a[0] * a[6] * a[11] - a[0] * a[7] * a[10] - a[4] * a[2] * a[11] + a[4] * a[3] * a[10] + a[8] * a[2] * a[7] - a[8] * a[3] * a[6];
    inv[11] = -a[0] * a[5] * a[11] + a[0] * a[7] * a[9] + a[4] * a[1] * a[11] - a[4] * a[3] * a[9] - a[8] * a[1] * a[7] + a[8] * a[3] * a[5];
    inv[15] = a[0] * a[5] * a[10] - a[0] * a[6] * a[9] - a[4] * a[1] * a[10] + a[4] * a[2] * a[9] + a[8] * a[1] * a[6] - a[8] * a[2] * a[5];
    const det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
    return inv.map(v => v / (det || 1)) as Float32Array;
}
function lookAt(eye: number[], target: number[], up: number[]) {
    const norm = (v: number[]) => { const l = Math.hypot(...v) || 1; return v.map(q => q / l); };
    const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const z = norm(eye.map((v, i) => v - target[i])), x = norm(cross(up, z)), y = cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
}
function perspective(fov: number, aspect: number, near: number, far: number) {
    const f = 1 / Math.tan(fov / 2), o = mat4();
    o[0] = f / aspect;
    o[5] = f;
    o[10] = (far + near) / (near - far);
    o[11] = -1;
    o[14] = 2 * far * near / (near - far);
    return o;
}
function ortho(width: number, height: number, near: number, far: number) {
    const o = mat4();
    o[0] = 2 / width;
    o[5] = 2 / height;
    o[10] = -2 / (far - near);
    o[14] = -(far + near) / (far - near);
    o[15] = 1;
    return o;
}
const angleDelta = (a: number, b: number) => Math.atan2(Math.sin(b - a), Math.cos(b - a));
export interface Rail3DScene {
    update(snapshot: Snapshot, selected: string): void;
    setCamera(mode: CameraMode): void;
    setTheme(theme: Theme): void;
    dispose(): void;
}
export function createRail3DScene(canvas: HTMLCanvasElement, overlay: HTMLElement, topology: Topology, onSelect: (id: string) => void): Rail3DScene {
    const context = canvas.getContext('webgl2', { antialias: true, powerPreference: 'high-performance' });
    if (!context)
        throw new Error(translate("WebGL2 недоступен: включите аппаратное ускорение в браузере."));
    const gl: WebGL2RenderingContext = context;
    const compile = (vs: string, fs: string) => {
        const program = gl.createProgram()!;
        for (const [type, source] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]] as const) {
            const shader = gl.createShader(type)!;
            gl.shaderSource(shader, source);
            gl.compileShader(shader);
            if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS))
                throw new Error(gl.getShaderInfoLog(shader) || 'shader');
            gl.attachShader(program, shader);
            gl.deleteShader(shader);
        }
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS))
            throw new Error(gl.getProgramInfoLog(program) || 'link');
        return program;
    };
    const program = compile(VS, FS), sky = compile(SKY_VS, SKY_FS);
    const uniform = (p: WebGLProgram, name: string) => gl.getUniformLocation(p, name);
    const U = Object.fromEntries(['uVP', 'uOffset', 'uSun', 'uSunColor', 'uSky', 'uGround', 'uFog', 'uEye', 'uOrigin', 'uFogDensity', 'uNight', 'uAO', 'uGroundTex', 'uShadow'].map(n => [n, uniform(program, n)]));
    const skyInv = uniform(sky, 'uInv'), skyTop = uniform(sky, 'uTop'), skyHorizon = uniform(sky, 'uHorizon');
    const skyVao = gl.createVertexArray();
    interface Gpu {
        vao: WebGLVertexArrayObject;
        count: number;
        instances: WebGLBuffer;
        capacity: number;
        buffers: WebGLBuffer[];
    }
    const upload = (data: MeshData, capacity = 1): Gpu => {
        const vao = gl.createVertexArray()!;
        gl.bindVertexArray(vao);
        const buffers = [data.positions, data.normals, data.colors].map((array, i) => {
            const buffer = gl.createBuffer()!;
            gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
            gl.bufferData(gl.ARRAY_BUFFER, array, gl.STATIC_DRAW);
            gl.enableVertexAttribArray(i);
            gl.vertexAttribPointer(i, i === 2 ? 4 : 3, gl.FLOAT, false, 0, 0);
            return buffer;
        });
        const instances = gl.createBuffer()!;
        gl.bindBuffer(gl.ARRAY_BUFFER, instances);
        gl.bufferData(gl.ARRAY_BUFFER, capacity * 16, gl.DYNAMIC_DRAW);
        gl.enableVertexAttribArray(3);
        gl.vertexAttribPointer(3, 4, gl.FLOAT, false, 16, 0);
        gl.vertexAttribDivisor(3, 1);
        gl.bindVertexArray(null);
        return { vao, count: data.positions.length / 3, instances, capacity, buffers };
    };
    const scratch = new Float32Array(4096 * 4);
    const draw = (mesh: Gpu, list: Instance[]) => {
        if (!list.length)
            return;
        gl.bindVertexArray(mesh.vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, mesh.instances);
        const n = Math.min(list.length, 4096);
        if (n > mesh.capacity) {
            mesh.capacity = Math.max(n, mesh.capacity * 2);
            gl.bufferData(gl.ARRAY_BUFFER, mesh.capacity * 16, gl.DYNAMIC_DRAW);
        }
        for (let i = 0; i < n; i++) {
            const p = list[i];
            scratch[i * 4] = p.x;
            scratch[i * 4 + 1] = p.y;
            scratch[i * 4 + 2] = p.z;
            scratch[i * 4 + 3] = p.yaw;
        }
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, scratch, 0, n * 4);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, mesh.count, n);
    };
    const route = buildRoute(topology);
    const vehicles = Object.fromEntries((Object.keys(VEHICLES) as VehicleKind[]).map(k => [k, upload({ locomotive: createLocomotive, coal: createCoalWagon, boxcar: createBoxcar, coach: createCoach }[k](), 128)])) as Record<VehicleKind, Gpu>;
    const shadows = Object.fromEntries((Object.keys(VEHICLES) as VehicleKind[]).map(k => [k, upload(createShadow(k), 128)])) as Record<VehicleKind, Gpu>;
    const stationMeshes = { small: upload(createStation('station-small'), 8), terminal: upload(createStation('station-terminal'), 4) };
    const chunks = buildTrackChunks(route).map(c => ({ ...c, gpu: upload(c.mesh) }));
    const sleeper = upload(createSleeper(), 4096), mast = upload(createMast(), 256), ground = upload(createGround(16000));
    const stationPoses = route.stations.map(s => ({ ...route.pose(s.dist, 40), terminal: s.terminal, name: s.name }));
    // Live train state: smoothed head position and passing-loop assignment.
    interface Live {
        train: Train;
        from: number;
        to: number;
        t0: number;
        duration: number;
        zone?: Zone;
        loop: number;
        consist: ReturnType<typeof consistFor>;
    }
    const live = new Map<string, Live>();
    let selected = '', lastUpdate = performance.now(), mode: CameraMode = '3d', theme: Theme = 'light', disposed = false;
    let zoom = { '2d': 340, '3d': 78 }, camYaw = NaN, target: [
        number,
        number
    ] | null = null;
    const headDist = (l: Live, now: number) => l.from + (l.to - l.from) * Math.min(1, (now - l.t0) / l.duration);
    const tailSpan = (l: Live, head: number) => { const back = l.consist.at(-1)!.offset + 15; const dir = l.train.direction > 0 ? 1 : -1; return [Math.min(head, head - dir * back), Math.max(head, head - dir * back)]; };
    function update(snapshot: Snapshot, selection: string) {
        const now = performance.now(), interval = Math.min(1500, Math.max(250, now - lastUpdate));
        lastUpdate = now;
        selected = selection;
        for (const train of snapshot.trains) {
            const dist = route.chainToDist(train.position_m);
            let entry = live.get(train.id);
            if (!entry || entry.train.length_m !== train.length_m || entry.train.type !== train.type) {
                entry = { train, from: dist, to: dist, t0: now, duration: interval, loop: 0, consist: consistFor(train) };
                live.set(train.id, entry);
            }
            else {
                const current = headDist(entry, now);
                Object.assign(entry, { train, from: Math.abs(dist - current) > 4000 ? dist : current, to: dist, t0: now, duration: interval });
            }
        }
        for (const id of [...live.keys()])
            if (!snapshot.trains.some(t => t.id === id))
                live.delete(id);
        // Standing trains take a free passing loop so moving trains can pass on the main line.
        for (const entry of live.values()) {
            const [lo, hi] = tailSpan(entry, entry.to);
            if (entry.zone && (hi < entry.zone.from || lo > entry.zone.to)) {
                entry.zone = undefined;
                entry.loop = 0;
            }
            const stationIndex = topology.stations.findIndex(s => s.id === entry.train.station_id);
            if (!entry.zone && entry.train.status !== 'moving' && stationIndex >= 0) {
                const zone = route.zones[stationIndex];
                const used = new Set([...live.values()].filter(o => o !== entry && o.zone === zone).map(o => o.loop));
                let loop = 1;
                while (used.has(loop) && loop < zone.loops)
                    loop++;
                if (!used.has(loop)) {
                    entry.zone = zone;
                    entry.loop = loop;
                }
            }
        }
    }
    // Train-number and station labels, positioned in screen space every frame.
    const labels = new Map<string, HTMLButtonElement>();
    const stationLabels = route.stations.map(s => { const el = document.createElement('span'); el.className = 'r3d-station'; el.textContent = s.name; overlay.append(el); return el; });
    const labelFor = (train: Train) => {
        let el = labels.get(train.id);
        if (!el) {
            el = document.createElement('button');
            el.type = 'button';
            el.className = 'r3d-label';
            el.addEventListener('click', () => onSelect(train.id));
            overlay.append(el);
            labels.set(train.id, el);
        }
        el.textContent = `№ ${train.number}`;
        el.classList.toggle('freight', train.type === 'freight');
        el.classList.toggle('sel', train.id === selected);
        return el;
    };
    let frameId = 0, prev = performance.now();
    function frame(now: number) {
        frameId = requestAnimationFrame(frame);
        if (disposed || document.hidden)
            return;
        const dt = Math.min(.1, (now - prev) / 1000);
        prev = now;
        const ratio = Math.min(devicePixelRatio || 1, 2), w = Math.floor(canvas.clientWidth * ratio), h = Math.floor(canvas.clientHeight * ratio);
        if (!w || !h)
            return;
        if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
        }
        gl.viewport(0, 0, w, h);
        // Vehicle poses in world metres, then camera relative.
        const vehiclePoses: {
            kind: VehicleKind;
            x: number;
            z: number;
            yaw: number;
        }[] = [];
        const heads = new Map<string, {
            x: number;
            z: number;
            yaw: number;
        }>();
        for (const entry of live.values()) {
            const head = headDist(entry, now), dir = entry.train.direction > 0 ? 1 : -1;
            for (const car of entry.consist) {
                const d = head - dir * car.offset, span = VEHICLES[car.kind].bogies;
                const p = route.pose(d, span), lat = route.lateral(entry.zone, entry.loop, d);
                const slope = Math.atan2(route.lateral(entry.zone, entry.loop, d + span / 2) - route.lateral(entry.zone, entry.loop, d - span / 2), span);
                const pose = { kind: car.kind, x: p.x + Math.cos(p.yaw) * lat, z: p.z - Math.sin(p.yaw) * lat, yaw: p.yaw + slope + (dir < 0 ? Math.PI : 0) };
                vehiclePoses.push(pose);
                if (car.offset === 0)
                    heads.set(entry.train.id, pose);
            }
        }
        const focus = heads.get(selected) ?? heads.values().next().value;
        if (focus) {
            if (!target)
                target = [focus.x, focus.z];
            const k = Math.min(1, dt * 8);
            target = [target[0] + (focus.x - target[0]) * k, target[1] + (focus.z - target[1]) * k];
            if (Math.hypot(focus.x - target[0], focus.z - target[1]) > 500)
                target = [focus.x, focus.z];
            camYaw = Number.isNaN(camYaw) ? focus.yaw : camYaw + angleDelta(camYaw, focus.yaw) * Math.min(1, dt * 2.2);
        }
        // Without live trains (reconnecting, empty snapshot) keep showing the first station.
        if (!target)
            target = [stationPoses[0].x, stationPoses[0].z];
        const origin = target, rel = (x: number, z: number) => [x - origin[0], z - origin[1]];
        // Fixed cameras: 3D is a three-quarter view ahead of the locomotive, 2D is top-down with north up.
        const aspect = w / h, T = THEMES[theme];
        let vp: Float32Array, eye: number[], fogDensity = 0;
        if (mode === '3d') {
            const distance = zoom['3d'], elevation = .3, side = camYaw + .62;
            eye = [Math.sin(side) * Math.cos(elevation) * distance, Math.sin(elevation) * distance + 2, Math.cos(side) * Math.cos(elevation) * distance];
            const look = [-Math.sin(camYaw) * distance * .12, 2.2, -Math.cos(camYaw) * distance * .12];
            vp = multiply(perspective(.72, aspect, .5, 9000), lookAt(eye, look, [0, 1, 0]));
            fogDensity = 1 / (2200 + distance * 6);
        }
        else {
            const span = zoom['2d'];
            eye = [0, 1500, 0];
            vp = multiply(ortho(span * aspect, span, 10, 4000), lookAt(eye, [0, 0, 0], [0, 0, -1]));
        }
        const range = mode === '3d' ? Math.max(2600, zoom['3d'] * 30) : zoom['2d'] * aspect * .8 + 60;
        gl.clearColor(T.fog[0] ** (1 / 2.2), T.fog[1] ** (1 / 2.2), T.fog[2] ** (1 / 2.2), 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        if (mode === '3d') {
            gl.disable(gl.DEPTH_TEST);
            gl.useProgram(sky);
            gl.bindVertexArray(skyVao);
            gl.uniformMatrix4fv(skyInv, false, invert(vp));
            gl.uniform3fv(skyTop, T.top);
            gl.uniform3fv(skyHorizon, T.fog);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
        }
        gl.enable(gl.DEPTH_TEST);
        gl.disable(gl.CULL_FACE);
        gl.useProgram(program);
        gl.uniformMatrix4fv(U.uVP, false, vp);
        const sun = T.sun, l = Math.hypot(...sun);
        gl.uniform3f(U.uSun, sun[0] / l, sun[1] / l, sun[2] / l);
        gl.uniform3fv(U.uSunColor, T.sunColor);
        gl.uniform3fv(U.uSky, T.sky);
        gl.uniform3fv(U.uGround, T.ground);
        gl.uniform3fv(U.uFog, T.fog);
        gl.uniform3fv(U.uEye, eye);
        gl.uniform3f(U.uOrigin, origin[0], 0, origin[1]);
        gl.uniform1f(U.uFogDensity, fogDensity);
        gl.uniform1f(U.uNight, T.night);
        gl.uniform1f(U.uShadow, theme === 'dark' ? .5 : 1);
        const at0 = { x: 0, y: 0, z: 0, yaw: 0 };
        // Ground follows the camera in 10 m steps so its noise pattern stays fixed to the world.
        const gx = Math.round(origin[0] / 10) * 10, gz = Math.round(origin[1] / 10) * 10;
        gl.uniform1f(U.uAO, 0);
        gl.uniform1f(U.uGroundTex, 1);
        gl.uniform3f(U.uOffset, gx - origin[0], 0, gz - origin[1]);
        draw(ground, [at0]);
        gl.uniform1f(U.uGroundTex, 0);
        for (const c of chunks) {
            if (Math.hypot(c.center[0] - origin[0], c.center[1] - origin[1]) - c.radius > range)
                continue;
            gl.uniform3f(U.uOffset, c.origin[0] - origin[0], 0, c.origin[1] - origin[1]);
            draw(c.gpu, [at0]);
        }
        gl.uniform3f(U.uOffset, 0, 0, 0);
        // Sleepers and masts near the camera.
        const near = Math.min(range, mode === '3d' ? 420 : zoom['2d'] * aspect * .75 + 40);
        const centerDist = (() => {
            let best = 0, bestD = Infinity;
            for (let d = -1600; d < route.length + 1600; d += 200) {
                const p = route.point(d), e = Math.hypot(p[0] - origin[0], p[1] - origin[1]);
                if (e < bestD) {
                    bestD = e;
                    best = d;
                }
            }
            return best;
        })();
        const sleepers: Instance[] = [], masts: Instance[] = [];
        const paths: {
            zone?: Zone;
            loop: number;
        }[] = [{ loop: 0 }, ...route.zones.flatMap(z => Array.from({ length: z.loops }, (_, i) => ({ zone: z, loop: i + 1 })))];
        for (const path of paths) {
            const from = Math.max(centerDist - near - 300, path.zone?.from ?? -1600), to = Math.min(centerDist + near + 300, path.zone?.to ?? route.length + 1600);
            for (let d = Math.ceil(from / .6) * .6; d < to && sleepers.length < 4000; d += .6) {
                const p = route.pose(d, 4), lat = route.lateral(path.zone, path.loop, d);
                const x = p.x + Math.cos(p.yaw) * lat, z = p.z - Math.sin(p.yaw) * lat;
                if (Math.hypot(x - origin[0], z - origin[1]) > near)
                    continue;
                const [rx, rz] = rel(x, z);
                sleepers.push({ x: rx, y: 0, z: rz, yaw: p.yaw });
            }
        }
        for (let d = Math.ceil((centerDist - range) / 62) * 62; d < centerDist + range; d += 62) {
            if (route.zones.some(z => d > z.from - 20 && d < z.to + 20))
                continue;
            const p = route.pose(d, 4), x = p.x - Math.cos(p.yaw) * 3.4, z = p.z + Math.sin(p.yaw) * 3.4;
            if (Math.hypot(x - origin[0], z - origin[1]) > range)
                continue;
            const [rx, rz] = rel(x, z);
            masts.push({ x: rx, y: 0, z: rz, yaw: p.yaw });
        }
        draw(sleeper, sleepers);
        draw(mast, masts);
        for (const kind of ['small', 'terminal'] as const) {
            draw(stationMeshes[kind], stationPoses.filter(s => (kind === 'terminal') === s.terminal && Math.hypot(s.x - origin[0], s.z - origin[1]) < range + 200).map(s => { const [x, z] = rel(s.x, s.z); return { x, y: 0, z, yaw: s.yaw }; }));
        }
        gl.uniform1f(U.uAO, 1);
        const byKind = (kind: VehicleKind) => vehiclePoses.filter(p => p.kind === kind && Math.hypot(p.x - origin[0], p.z - origin[1]) < range).map(p => { const [x, z] = rel(p.x, p.z); return { x, y: 0, z, yaw: p.yaw }; });
        const visible = Object.fromEntries((Object.keys(VEHICLES) as VehicleKind[]).map(k => [k, byKind(k)])) as Record<VehicleKind, Instance[]>;
        for (const kind of Object.keys(VEHICLES) as VehicleKind[])
            draw(vehicles[kind], visible[kind]);
        // Soft contact shadows under every vehicle, blended over the track.
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.depthMask(false);
        gl.enable(gl.POLYGON_OFFSET_FILL);
        gl.polygonOffset(-2, -2);
        gl.uniform1f(U.uAO, 0);
        for (const kind of Object.keys(VEHICLES) as VehicleKind[])
            draw(shadows[kind], visible[kind]);
        gl.disable(gl.POLYGON_OFFSET_FILL);
        gl.depthMask(true);
        gl.disable(gl.BLEND);
        gl.bindVertexArray(null);
        // Screen-space labels.
        const project = (x: number, y: number, z: number) => {
            const [rx, rz] = rel(x, z), v = [rx, y, rz, 1], q = [0, 0, 0, 0];
            for (let r = 0; r < 4; r++)
                for (let k = 0; k < 4; k++)
                    q[r] += vp[k * 4 + r] * v[k];
            return q[3] <= 0 ? null : { x: (q[0] / q[3] * .5 + .5) * canvas.clientWidth, y: (-.5 * q[1] / q[3] + .5) * canvas.clientHeight, depth: q[2] / q[3] };
        };
        const place = (el: HTMLElement, p: ReturnType<typeof project>) => {
            const show = !!p && p.depth < 1 && p.x > -40 && p.x < canvas.clientWidth + 40 && p.y > -20 && p.y < canvas.clientHeight + 20;
            el.hidden = !show;
            if (show)
                el.style.transform = `translate(${p!.x.toFixed(1)}px,${p!.y.toFixed(1)}px)`;
        };
        for (const entry of live.values()) {
            const head = heads.get(entry.train.id), el = labelFor(entry.train);
            place(el, head && Math.hypot(head.x - origin[0], head.z - origin[1]) < range ? project(head.x, mode === '3d' ? 6.2 : 0, head.z) : null);
        }
        stationPoses.forEach((s, i) => { const px = s.x + Math.cos(s.yaw) * 14, pz = s.z - Math.sin(s.yaw) * 14; place(stationLabels[i], Math.hypot(px - origin[0], pz - origin[1]) < range ? project(px, mode === '3d' ? (s.terminal ? 16 : 9) : 0, pz) : null); });
    }
    frameId = requestAnimationFrame(frame);
    const wheel = (event: WheelEvent) => { event.preventDefault(); const limits = { '2d': [60, 8000], '3d': [22, 900] }[mode]; zoom = { ...zoom, [mode]: Math.max(limits[0], Math.min(limits[1], zoom[mode] * Math.exp(event.deltaY * .0012))) }; };
    canvas.addEventListener('wheel', wheel, { passive: false });
    return {
        update,
        setCamera(next) { mode = next; },
        setTheme(next) { theme = next; },
        dispose() { disposed = true; cancelAnimationFrame(frameId); canvas.removeEventListener('wheel', wheel); overlay.replaceChildren(); },
    };
}
