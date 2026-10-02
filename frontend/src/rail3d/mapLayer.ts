import {translate, displayText} from '../i18n/core.ts';
import type { CustomLayerInterface, Map as RailMap } from 'maplibre-gl';
import type { Snapshot, Topology } from '../types';
import type { MeshData } from './mesh';
import { createBoxcar, createCoach, createCoalWagon, createLocomotive, createStation, VEHICLES, type VehicleKind } from './models';
import { VS, FS, THEMES, consistFor } from './materials';
import { modelMatrix, trackYaw, type Coordinate } from './geography';
import type { DisplayTrain, StationTracks } from './stationTracks';
import { railChunks, railMesh, RAIL_ALTITUDE } from './railGeometry';
export const TRAIN_LAYER = 'railflow-3d';
export const MODEL_ZOOM = 14;
type ModelKind = VehicleKind | 'station-small' | 'station-terminal';
interface Item {
    kind: ModelKind;
    coordinate: Coordinate;
    yaw: number;
    station?: boolean;
}
interface Gpu {
    vao: WebGLVertexArrayObject;
    count: number;
    buffers: WebGLBuffer[];
}
/** Draw into MapLibre's canvas/depth buffer. No second camera, canvas or animation clock. */
export function createTrainLayer(topology: Topology, onError: (message: string) => void, layout: StationTracks) {
    const route = layout.route;
    let map: RailMap, gl: WebGL2RenderingContext, program: WebGLProgram | null = null;
    let enabled = false, theme: 'light' | 'dark' = 'light', items: Item[] = [];
    let gpu: Partial<Record<ModelKind, Gpu>> = {};
    const buffers = new Set<WebGLBuffer>(), vaos = new Set<WebGLVertexArrayObject>();
    const railCache = new Map<string, Gpu>();
    let chunks = railChunks(layout), trackSignature = '';
    let uniforms: Record<string, WebGLUniformLocation | null> = {};
    const consists = new Map<string, {
        signature: string;
        cars: ReturnType<typeof consistFor>;
    }>();
    const stations: Item[] = topology.stations.map((s, i) => {
        const d = route.distance(s.position_m);
        return { kind: i === 0 || i === topology.stations.length - 1 ? 'station-terminal' : 'station-small', coordinate: s.coordinate,
            yaw: trackYaw(route.point(d - 3), route.point(d + 3)), station: true };
    });
    function disposeGpu() {
        for (const b of buffers)
            gl.deleteBuffer(b);
        for (const v of vaos)
            gl.deleteVertexArray(v);
        if (program)
            gl.deleteProgram(program);
        buffers.clear();
        vaos.clear();
        railCache.clear();
        gpu = {};
        program = null;
    }
    function discard(mesh: Gpu) {
        gl.deleteVertexArray(mesh.vao);
        vaos.delete(mesh.vao);
        for (const b of mesh.buffers) {
            gl.deleteBuffer(b);
            buffers.delete(b);
        }
    }
    function upload(data: MeshData): Gpu {
        const vao = gl.createVertexArray();
        if (!vao)
            throw Error('Cannot allocate 3D mesh');
        vaos.add(vao);
        gl.bindVertexArray(vao);
        const meshBuffers = [data.positions, data.normals, data.colors].map((values, i) => {
            const buffer = gl.createBuffer();
            if (!buffer)
                throw Error('Cannot allocate 3D buffer');
            buffers.add(buffer);
            gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
            gl.bufferData(gl.ARRAY_BUFFER, values, gl.STATIC_DRAW);
            gl.enableVertexAttribArray(i);
            gl.vertexAttribPointer(i, i === 2 ? 4 : 3, gl.FLOAT, false, 0, 0);
            return buffer;
        });
        gl.disableVertexAttribArray(3);
        gl.bindVertexArray(null);
        return { vao, count: data.positions.length / 3, buffers: meshBuffers };
    }
    function initialize() {
        try {
            disposeGpu();
            program = gl.createProgram();
            if (!program)
                throw Error('Cannot allocate 3D program');
            for (const [type, source] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]] as const) {
                const shader = gl.createShader(type);
                if (!shader)
                    throw Error('Cannot allocate 3D shader');
                gl.shaderSource(shader, source);
                gl.compileShader(shader);
                const ok = gl.getShaderParameter(shader, gl.COMPILE_STATUS), error = gl.getShaderInfoLog(shader);
                if (ok)
                    gl.attachShader(program, shader);
                gl.deleteShader(shader);
                if (!ok)
                    throw Error(error || '3D shader compilation failed');
            }
            gl.linkProgram(program);
            if (!gl.getProgramParameter(program, gl.LINK_STATUS))
                throw Error(gl.getProgramInfoLog(program) || '3D shader linking failed');
            uniforms = Object.fromEntries(['uVP', 'uOffset', 'uSun', 'uSunColor', 'uSky', 'uGround', 'uFog', 'uEye', 'uOrigin', 'uFogDensity', 'uNight', 'uAO', 'uGroundTex', 'uShadow'].map(n => [n, gl.getUniformLocation(program!, n)]));
            gpu = { locomotive: upload(createLocomotive()), coal: upload(createCoalWagon()), boxcar: upload(createBoxcar()), coach: upload(createCoach()),
                'station-small': upload(createStation('station-small')), 'station-terminal': upload(createStation('station-terminal')) };
            onError('');
        }
        catch (error) {
            disposeGpu();
            onError(translate('3D недоступен. Карта работает: {0}', (error as Error).message));
        }
        finally {
            gl.bindVertexArray(null);
            gl.bindBuffer(gl.ARRAY_BUFFER, null);
        }
    }
    const restored = () => { initialize(); map.triggerRepaint(); };
    const layer: CustomLayerInterface = {
        id: TRAIN_LAYER, type: 'custom', renderingMode: '3d',
        onAdd(m, context) {
            map = m;
            if (!('createVertexArray' in context)) {
                onError(translate("3D требует WebGL2. Карта и данные доступны в 2D."));
                return;
            }
            gl = context as WebGL2RenderingContext;
            initialize();
            map.getCanvas().addEventListener('webglcontextrestored', restored);
        },
        render(_context, args) {
            if (!enabled || !program || map.getZoom() < MODEL_ZOOM)
                return;
            const light = THEMES[theme], u = uniforms;
            const frontFace = gl.getParameter(gl.FRONT_FACE) as number;
            // Swapping model Y/Z for Mercator reverses winding. Keep outward-facing lighting.
            gl.frontFace(gl.CW);
            gl.useProgram(program);
            gl.disable(gl.CULL_FACE);
            gl.disable(gl.STENCIL_TEST);
            gl.enable(gl.DEPTH_TEST);
            gl.depthMask(true);
            gl.depthFunc(gl.LEQUAL);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            for (const [name, value] of Object.entries({ uSun: light.sun, uSunColor: light.sunColor, uSky: light.sky, uGround: light.ground, uFog: light.fog, uEye: [0, 100, -150], uOrigin: [0, 0, 0] }))
                gl.uniform3fv(u[name], value);
            gl.uniform1f(u.uNight, light.night);
            gl.uniform1f(u.uAO, 1);
            gl.uniform1f(u.uFogDensity, 0);
            gl.uniform1f(u.uGroundTex, 0);
            gl.uniform1f(u.uShadow, 0);
            const canvas = map.getCanvas(), width = canvas.clientWidth, height = canvas.clientHeight;
            const bounds = map.getBounds(), sleepers = map.getZoom() >= 17;
            const center = map.getCenter();
            const visible = chunks.filter(c => c.bounds[2] >= bounds.getWest() - .001 && c.bounds[0] <= bounds.getEast() + .001 && c.bounds[3] >= bounds.getSouth() - .001 && c.bounds[1] <= bounds.getNorth() + .001)
                .sort((a, b) => Math.hypot(a.origin[0] - center.lng, a.origin[1] - center.lat) - Math.hypot(b.origin[0] - center.lng, b.origin[1] - center.lat)).slice(0, 128);
            let built = 0, pending = false;
            gl.uniform1f(u.uAO, 0);
            gl.uniform3f(u.uOffset, 0, 0, 0);
            for (const chunk of visible) {
                const key = `${chunk.id}:${sleepers}`;
                let mesh = railCache.get(key);
                if (!mesh) {
                    if (built >= 2) {
                        pending = true;
                        mesh = railCache.get(`${chunk.id}:${!sleepers}`);
                        if (!mesh)
                            continue;
                    }
                    else {
                        mesh = upload(railMesh(chunk, layout, sleepers));
                        railCache.set(key, mesh);
                        built++;
                    }
                }
                else {
                    railCache.delete(key);
                    railCache.set(key, mesh);
                }
                gl.uniformMatrix4fv(u.uVP, false, modelMatrix(args.defaultProjectionData.mainMatrix, chunk.origin, 0, RAIL_ALTITUDE));
                gl.bindVertexArray(mesh.vao);
                gl.vertexAttrib4f(3, 0, 0, 0, 0);
                gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
            }
            while (railCache.size > 160) {
                const key = railCache.keys().next().value!;
                discard(railCache.get(key)!);
                railCache.delete(key);
            }
            if (pending)
                map.triggerRepaint();
            gl.uniform1f(u.uAO, 1);
            for (const item of [...stations, ...items]) {
                const screen = map.project(item.coordinate);
                if (screen.x < -160 || screen.x > width + 160 || screen.y < -160 || screen.y > height + 160)
                    continue;
                const mesh = gpu[item.kind];
                if (!mesh)
                    continue;
                gl.uniformMatrix4fv(u.uVP, false, modelMatrix(args.defaultProjectionData.mainMatrix, item.coordinate, 0, RAIL_ALTITUDE));
                gl.uniform3f(u.uOffset, item.station ? -25 * Math.cos(item.yaw) : 0, 0, item.station ? 25 * Math.sin(item.yaw) : 0);
                gl.bindVertexArray(mesh.vao);
                gl.vertexAttrib4f(3, 0, 0, 0, item.yaw);
                gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
            }
            gl.bindVertexArray(null);
            gl.frontFace(frontFace);
        },
        onRemove() {
            if (gl) {
                map.getCanvas().removeEventListener('webglcontextrestored', restored);
                disposeGpu();
            }
        },
    };
    return { layer,
        update(snapshot: Snapshot) {
            // The shared presentation clock supplies the same interpolated snapshot as the 2D markers.
            for (const id of consists.keys())
                if (!snapshot.trains.some(t => t.id === id))
                    consists.delete(id);
            const signature = layout.yards.map(y => layout.trackCount(y)).join(':');
            if (signature !== trackSignature) {
                chunks = railChunks(layout);
                trackSignature = signature;
            }
            items = (snapshot.trains as DisplayTrain[]).flatMap(train => {
                const signature = `${train.type}:${train.length_m}`;
                let consist = consists.get(train.id);
                if (!consist || consist.signature !== signature) {
                    consist = { signature, cars: consistFor(train) };
                    consists.set(train.id, consist);
                }
                const head = route.distance(train.position_m);
                return consist.cars.map(({ kind, offset }) => {
                    const d = head - train.direction * offset, span = VEHICLES[kind].bogies;
                    return { kind, coordinate: offset === 0 ? train.coordinate : layout.point(d, train.displayTrack),
                        yaw: trackYaw(layout.point(d - span / 2, train.displayTrack), layout.point(d + span / 2, train.displayTrack), train.direction) };
                });
            });
            map?.triggerRepaint();
        },
        configure(active: boolean, nextTheme: 'light' | 'dark') { enabled = active; theme = nextTheme; map?.triggerRepaint(); },
    };
}
