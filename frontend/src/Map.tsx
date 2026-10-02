import {translate, displayText, intlLocale} from './i18n/core.ts';
import {useLanguage} from './language';
import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl, { type GeoJSONSourceSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { Topology, Snapshot } from './types';
import { useDispatch } from './store';
import { committedSnapshot, paintIdentifier } from './performanceProbe';
import { useTheme } from './theme';
import { createTrainLayer, MODEL_ZOOM } from './rail3d/mapLayer';
import { createTrainMotion } from './trainMotion';
import { createStationTracks } from './rail3d/stationTracks';
import { followCenter } from './cameraFollow';
const trainStatus: Record<string, string> = { get waiting() {
        return translate("На станции");
    }, get moving() {
        return translate("В движении");
    }, get completed() {
        return translate("Прибыл");
    } };
let networkRequest: Promise<Extract<GeoJSONSourceSpecification['data'], {
    type: 'FeatureCollection';
}>> | null = null;
function loadNetwork() {
    if (!networkRequest)
        networkRequest = fetch('/api/network').then(r => {
            if (!r.ok)
                throw Error('Network unavailable');
            return r.json();
        }).catch(error => { networkRequest = null; throw error; });
    return networkRequest;
}
export function RailMap({ topology, snapshot, allCountry, mode, active, follow, onFollowChange, historical = false, focusRequest = 0 }: {
    topology: Topology;
    snapshot: Snapshot;
    allCountry: boolean;
    mode: '2d' | '3d';
    active: boolean;
    follow: boolean;
    onFollowChange: (follow: boolean) => void;
    historical?: boolean;
    focusRequest?: number;
}) {
    const language = useLanguage();
    const stationLabels = useRef<(() => void)[]>([]);
    const container = useRef<HTMLDivElement>(null), map = useRef<maplibregl.Map | null>(null);
    const markers = useRef(new Map<string, maplibregl.Marker>());
    const retryNetwork = useRef<() => void>(() => { });
    const { selected, select } = useDispatch();
    const { theme } = useTheme();
    const layer = useRef<ReturnType<typeof createTrainLayer> | null>(null);
    const current = useRef({ snapshot, selected, follow, onFollowChange, mode, active });
    current.current = { snapshot, selected, follow, onFollowChange, mode, active };
    const motion = useMemo(() => createTrainMotion(topology), [topology]);
    const tracks = useMemo(() => createStationTracks(topology), [topology]);
    const focusing = useRef(false);
    const drawn = useRef(snapshot.trains), repaint = useRef<() => void>(() => { });
    const [modelError, setModelError] = useState(''), [closeZoom, setCloseZoom] = useState(false);
    const [ready, setReady] = useState(false), [networkError, setNetworkError] = useState(false), [networkState, setNetworkState] = useState(translate("Загрузка сети Казахстана…"));
    useEffect(() => {
        if (!container.current)
            return;
        setReady(false);
        const m = new maplibregl.Map({ container: container.current, style: { version: 8, sources: { base: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } }, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eef1eb' } }, { id: 'base', source: 'base', type: 'raster', paint: { 'raster-saturation': -.85, 'raster-opacity': .53 } }] }, center: [70.5, 52.25], zoom: 7.1, attributionControl: { compact: true } });
        map.current = m;
        m.transformCameraUpdate = () => {
            const view = current.current, train = drawn.current.find(t => t.id === view.selected);
            const center = followCenter(view.active, view.follow, focusing.current, train?.coordinate);
            return center ? { center: maplibregl.LngLat.convert(center) } : {};
        };
        m.on('moveend', () => { focusing.current = false; });
        m.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'bottom-right');
        m.on('dragstart', event => {
            if (event.originalEvent)
                current.current.onFollowChange(false);
        });
        m.on('zoomend', () => setCloseZoom(m.getZoom() >= MODEL_ZOOM));
        m.on('rotate', () => {
            for (const train of drawn.current) {
                const arrow = markers.current.get(train.id)?.getElement().querySelector('.train-bearing') as HTMLElement | undefined;
                if (arrow)
                    arrow.style.transform = `rotate(${train.bearing_deg - m.getBearing()}deg)`;
            }
        });
        // Rail data does not wait for external raster tiles to finish loading.
        m.once('style.load', () => {
            m.addSource('corridor', { type: 'geojson', data: { type: 'FeatureCollection', features: topology.sections.map(s => ({ type: 'Feature', properties: { id: s.id, status: 'open' }, geometry: { type: 'LineString', coordinates: s.geometry } })) } });
            m.addLayer({ id: 'corridor-halo', source: 'corridor', type: 'line', paint: { 'line-color': '#ffffff', 'line-width': 7 } });
            m.addLayer({ id: 'corridor', source: 'corridor', type: 'line', paint: { 'line-color': ['match', ['get', 'status'], 'closed', '#dd6652', 'signal_failure', '#d59831', 'occupied', '#167466', '#228b79'], 'line-width': 3 } });
            m.addSource('station-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: tracks.features() } });
            m.addLayer({ id: 'station-tracks', source: 'station-tracks', type: 'line', minzoom: 12, paint: { 'line-color': '#72848a', 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1, 18, 3] } });
            layer.current = createTrainLayer(topology, setModelError, tracks);
            m.addLayer(layer.current.layer);
            for (const station of topology.stations) {
                const element = document.createElement('button');
                element.type = 'button';
                element.className = 'map-station station-button';
                const dot = document.createElement('i'), label = document.createElement('span');
                element.append(dot, label);
                const details = document.createElement('div');
                details.className = 'station-popup';
                const title = document.createElement('strong'), description = document.createElement('p');
                const updateLabel = () => {
                    element.setAttribute('aria-label', translate('Станция {0}', station.name));
                    label.textContent = displayText(station.name);
                    title.textContent = displayText(station.name);
                    description.textContent = translate('{0} км · {1} путей в демомодели. Станционная вместимость условная.', (station.position_m / 1000).toFixed(1), station.tracks);
                };
                updateLabel();
                stationLabels.current.push(updateLabel);
                details.append(title, description);
                new maplibregl.Marker({ element, anchor: 'left', offset: [-4, 0] }).setLngLat(station.coordinate).setPopup(new maplibregl.Popup({ offset: 10 }).setDOMContent(details)).addTo(m);
            }
            const requestNetwork = () => {
                setNetworkError(false);
                setNetworkState(translate("Загрузка сети Казахстана…"));
                loadNetwork().then(data => {
                    if (map.current !== m)
                        return;
                    if (!m.getSource('network')) {
                        m.addSource('network', { type: 'geojson', data });
                        m.addLayer({ id: 'network', source: 'network', type: 'line', paint: { 'line-color': '#526e7d', 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 1, 10, 2, 15, 3], 'line-opacity': .9 } }, 'corridor-halo');
                    }
                    setNetworkState(translate('{0} сегментов · OpenStreetMap', data.features.length.toLocaleString(intlLocale())));
                }).catch(() => {
                    if (map.current === m) {
                        setNetworkError(true);
                        setNetworkState(translate("Не удалось загрузить сеть Казахстана"));
                    }
                });
            };
            retryNetwork.current = requestNetwork;
            requestNetwork();
            setReady(true);
        });
        const resize = new ResizeObserver(() => m.resize());
        resize.observe(container.current);
        return () => { resize.disconnect(); markers.current.clear(); stationLabels.current = []; m.remove(); map.current = null; layer.current = null; };
    }, [topology, tracks]);
    useEffect(() => {
        stationLabels.current.forEach(update => update());
        const controls = [
            ['.maplibregl-ctrl-zoom-in', translate('Приблизить')],
            ['.maplibregl-ctrl-zoom-out', translate('Отдалить')],
            ['.maplibregl-ctrl-compass', translate('Север сверху')],
        ];
        for (const [selector, title] of controls) {
            const button = container.current?.querySelector(selector);
            button?.setAttribute('title', title);
            button?.setAttribute('aria-label', title);
        }
    }, [language, ready]);
    useEffect(() => {
        if (!ready)
            return;
        const bounds = new maplibregl.LngLatBounds();
        if (allCountry) {
            bounds.extend([46, 40.3]);
            bounds.extend([88, 56]);
        }
        else
            topology.sections.forEach(s => s.geometry.forEach(p => bounds.extend(p)));
        map.current?.fitBounds(bounds, { padding: 55, duration: 700, pitch: 0, bearing: 0 });
    }, [ready, allCountry, topology]);
    useEffect(() => {
        const m = map.current;
        if (!ready || !m)
            return;
        const train = drawn.current.find(t => t.id === current.current.selected) ?? current.current.snapshot.trains[0];
        if (mode === '3d' && train)
            m.easeTo({ center: train.coordinate, zoom: Math.max(m.getZoom(), 17), pitch: 60, duration: 700 });
        else
            m.easeTo({ pitch: 0, bearing: 0, duration: 500 });
    }, [ready, mode]);
    useEffect(() => {
        const m = map.current;
        if (!ready || !m || !follow || !active)
            return;
        const train = drawn.current.find(t => t.id === selected);
        if (train) {
            m.stop();
            focusing.current = true;
            m.easeTo({ center: train.coordinate, zoom: Math.max(m.getZoom(), mode === '3d' ? 17 : 16), pitch: mode === '3d' ? 60 : 0, duration: 600 });
        }
    }, [ready, follow, selected, active, mode, focusRequest]);
    useEffect(() => {
        const m = map.current;
        if (!ready || !m)
            return;
        layer.current?.configure(active && mode === '3d', theme);
        // Style only the basemap; a CSS canvas filter would also recolour the 3D trains.
        m.setPaintProperty('base', 'raster-brightness-max', theme === 'dark' ? .3 : 1);
        m.setPaintProperty('base', 'raster-saturation', theme === 'dark' ? -.65 : -.85);
        m.setPaintProperty('background', 'background-color', theme === 'dark' ? '#111c23' : '#eef1eb');
        m.setPaintProperty('corridor-halo', 'line-color', theme === 'dark' ? '#233a43' : '#ffffff');
    }, [ready, active, mode, theme]);
    useEffect(() => {
        const m = map.current;
        if (!ready || !m)
            return;
        let animation: number | null = null, followingJump = false, trackSignature = '';
        const paint = (now: number) => {
            animation = null;
            const view = current.current;
            if (!view.active || document.hidden)
                return;
            const frame = motion.sample(now);
            if (!frame.snapshot)
                return;
            const displayed = tracks.place(frame.snapshot);
            drawn.current = displayed.trains;
            const signature = tracks.yards.map(y => tracks.trackCount(y)).join(':');
            if (signature !== trackSignature) {
                (m.getSource('station-tracks') as maplibregl.GeoJSONSource)?.setData({ type: 'FeatureCollection', features: tracks.features() });
                trackSignature = signature;
            }
            for (const train of drawn.current) {
                const marker = markers.current.get(train.id);
                if (!marker)
                    continue;
                marker.setLngLat(train.coordinate);
                const arrow = marker.getElement().querySelector('.train-bearing') as HTMLElement;
                arrow.style.transform = `rotate(${train.bearing_deg - m.getBearing()}deg)`;
            }
            if (view.mode === '3d')
                layer.current?.update(displayed);
            const train = drawn.current.find(t => t.id === view.selected);
            if (view.follow && train && !m.isMoving()) {
                followingJump = true;
                m.jumpTo({ center: train.coordinate });
                followingJump = false;
            }
            if (frame.moving)
                animation = requestAnimationFrame(paint);
        };
        const wake = () => {
            if (animation === null)
                animation = requestAnimationFrame(paint);
        };
        const cameraEnd = () => {
            if (!followingJump)
                wake();
        };
        const visibility = () => {
            if (animation !== null)
                cancelAnimationFrame(animation);
            animation = null;
            // Returning from a suspended tab must not replay a backlog of train movements.
            motion.accept(current.current.snapshot, performance.now(), true);
            if (!document.hidden)
                wake();
        };
        repaint.current = wake;
        document.addEventListener('visibilitychange', visibility);
        m.on('moveend', cameraEnd);
        return () => {
            if (animation !== null)
                cancelAnimationFrame(animation);
            repaint.current = () => { };
            document.removeEventListener('visibilitychange', visibility);
            m.off('moveend', cameraEnd);
        };
    }, [ready, motion, tracks]);
    useEffect(() => {
        if (!ready)
            return;
        motion.accept(snapshot, performance.now(), historical || !active || document.hidden);
        if (historical)
            tracks.reset();
        repaint.current();
    }, [ready, snapshot, motion, tracks, historical, active]);
    useEffect(() => { repaint.current(); }, [mode, selected, follow]);
    useEffect(() => {
        const m = map.current;
        if (!ready || !m)
            return;
        (m.getSource('corridor') as maplibregl.GeoJSONSource)?.setData({ type: 'FeatureCollection', features: topology.sections.map(s => ({ type: 'Feature', properties: { id: s.id, status: snapshot.sections.find(x => x.id === s.id)?.status || 'open' }, geometry: { type: 'LineString', coordinates: s.geometry } })) });
        for (const [id, marker] of markers.current)
            if (!snapshot.trains.some(t => t.id === id)) {
                marker.remove();
                markers.current.delete(id);
            }
        snapshot.trains.forEach((train, index) => {
            let marker = markers.current.get(train.id);
            if (!marker) {
                const element = document.createElement('button');
                element.type = 'button';
                element.className = 'train-pin';
                element.addEventListener('click', () => select(train.id));
                const arrow = document.createElement('span');
                arrow.className = 'train-bearing';
                arrow.textContent = '↑';
                const label = document.createElement('span');
                label.className = 'train-label';
                element.append(arrow, label);
                marker = new maplibregl.Marker({ element, anchor: 'center', offset: [0, 0], subpixelPositioning: true }).setLngLat(train.coordinate).addTo(m);
                markers.current.set(train.id, marker);
            }
            const element = marker.getElement();
            element.classList.toggle('freight', train.type === 'freight');
            element.classList.toggle('selected', selected === train.id);
            element.classList.toggle('stopped', train.status !== 'moving');
            let label = element.querySelector('.train-label') as HTMLElement;
            const paintId = paintIdentifier(snapshot, 'map', train.id);
            if (paintId && label.getAttribute('elementtiming') !== paintId) {
                const replacement = label.cloneNode(false) as HTMLElement;
                replacement.setAttribute('elementtiming', paintId);
                label.replaceWith(replacement);
                label = replacement;
            }
            label.textContent = `${train.number} · ${trainStatus[train.status] || train.status}`;
            label.style.top = `${train.status === 'moving' ? -12 : (Math.floor(index / 2) - 1) * 25}px`;
            element.title = translate('№ {0} · {1} · {2} · {3} км/ч', train.number, train.route_name, trainStatus[train.status], (train.speed_mps * 3.6).toFixed(0));
            element.setAttribute('aria-label', element.title);
        });
        committedSnapshot(snapshot, 'map');
    }, [snapshot, ready, selected, topology, select, language]);
    const selectedTrain = snapshot.trains.find(t => t.id === selected);
    return <div className={'map-wrapper' + (!active ? ' viewer-hidden' : '')} aria-hidden={!active}><div ref={container} className="map-canvas"/>
 {displayText(mode === '3d' && <div className="viewer-hint" role="status">{displayText(modelError || (closeZoom ? translate("3D на карте · правая кнопка: поворот · колесо: масштаб") : translate("Приблизьте карту или включите «За поездом», чтобы увидеть 3D-составы")))}{displayText(!modelError && closeZoom && <span className="viewer-overlap">{translate("Станционные пути и распределение составов показаны условно.")}</span>)}</div>)}<div className="map-caption"><span className="dot green"/>{displayText(networkState)}{displayText(networkError && <button onClick={() => retryNetwork.current()}>{translate("Повторить")}</button>)}</div>{displayText(selectedTrain && <div className="map-selected"><strong>№ {displayText(selectedTrain.number)} · {displayText(trainStatus[selectedTrain.status])}</strong><span>{displayText(selectedTrain.route_name)}</span><small>{displayText((selectedTrain.speed_mps * 3.6).toFixed(0))}{translate(" км/ч · движение моделируется")}</small></div>)}<div className="map-distance"><strong>{displayText((topology.length_m / 1000).toFixed(1))}</strong><span>{translate("км маршрута")}</span></div></div>;
}
export function TrackDiagram({ topology, snapshot }: {
    topology: Topology;
    snapshot: Snapshot;
}) {
    const { selected, select } = useDispatch();
    const x = (p: number) => 75 + p / topology.length_m * 1050;
    return <div className="schematic"><svg viewBox="0 0 1200 350" aria-label={translate("Схема станций и перегонов")}>
 {displayText(topology.sections.map((section, i) => { const state = snapshot.sections.find(s => s.id === section.id); return <g key={section.id}><line x1={x(topology.stations[i].position_m)} y1="170" x2={x(topology.stations[i + 1].position_m)} y2="170" stroke={state?.status === 'closed' ? '#de6856' : state?.status === 'occupied' ? '#148976' : '#adc3c4'} strokeWidth="7"/><circle cx={(x(topology.stations[i].position_m) + x(topology.stations[i + 1].position_m)) / 2} cy="205" r="5" fill={state?.signal === 'green' ? '#148976' : '#de6856'}/><text x={(x(topology.stations[i].position_m) + x(topology.stations[i + 1].position_m)) / 2} y="232" textAnchor="middle" fontSize="12">{displayText((section.length_m / 1000).toFixed(1))}{translate(" км")}</text></g>; }))}
 {displayText(topology.stations.map(s => <g key={s.id}><path d={`M${x(s.position_m) - 25} 170 l12 -24 h26 l12 24 M${x(s.position_m) - 25} 170 l12 24 h26 l12 -24`} fill="none" stroke="#52767a" strokeWidth="3"/><circle cx={x(s.position_m)} cy="170" r="6" fill="white" stroke="#31565e" strokeWidth="3"/><text x={x(s.position_m)} y="285" textAnchor="middle" fontSize="15">{displayText(s.name)}</text><text x={x(s.position_m)} y="307" textAnchor="middle" fontSize="11" fill="#7c9093">{displayText(s.tracks)}{translate(" пути · стрелка ")}{displayText(snapshot.switches.find(w => w.station_id === s.id)?.available ? translate("свободна") : translate("занята"))}</text></g>))}
 {displayText(snapshot.trains.map((t, i) => <g key={t.id} role="button" tabIndex={0} aria-label={displayText(translate("Поезд {0}", t.number))} onClick={() => select(t.id)} onKeyDown={e => { if (e.key === 'Enter')
        select(t.id); }} style={{ cursor: 'pointer' }}><rect x={x(t.position_m) - 23} y={40 + Math.floor(i / 2) * 25} width="46" height="21" rx="5" fill={t.type === 'passenger' ? '#137f70' : '#d09939'} stroke={selected === t.id ? '#122f39' : 'none'} strokeWidth="3"/><text x={x(t.position_m)} y={55 + Math.floor(i / 2) * 25} textAnchor="middle" fontSize="12" fill="white">{displayText(t.direction > 0 ? '›' : '‹')}{displayText(t.number)}</text></g>))}
 </svg><p>{translate("Однопутная модель · два пути на промежуточных станциях · сигналы защищают перегоны")}</p></div>;
}
