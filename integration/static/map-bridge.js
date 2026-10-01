import { createLocator, trackIndex } from './coordinates.mjs';

// map and L are the original era map's existing Leaflet bindings.
const panel = document.createElement('section');
panel.id = 'railflow-live';
panel.setAttribute('aria-label', 'Движение из диспетчерской системы');
panel.dataset.connected = 'false';
panel.innerHTML = `<h2>Кокшетау ↔ Нурлы Жол</h2>
  <p id="railflow-connection" role="status">Подключение к диспетчеру…</p>
  <div id="railflow-values"><p id="railflow-clock">—</p><p id="railflow-metrics">—</p></div>
  <div class="actions"><button id="railflow-start" disabled>Запустить</button>
  <button id="railflow-pause" disabled>Пауза</button>
  <button id="railflow-fit">К маршруту</button><button id="railflow-dashboard">Диспетчер</button></div>
  <p class="note">Синтетическое движение. Поезда, события и планы общие с диспетчерской панелью.</p>`;
document.body.append(panel);

const connection = panel.querySelector('#railflow-connection');
const clock = panel.querySelector('#railflow-clock');
const metrics = panel.querySelector('#railflow-metrics');
const start = panel.querySelector('#railflow-start');
const pause = panel.querySelector('#railflow-pause');
const dashboard = () => {
  if (parent === window) location.href = '/#dispatcher';
  else parent.postMessage({ type: 'railflow.navigate', view: 'dispatcher' }, location.origin);
};
panel.querySelector('#railflow-dashboard').addEventListener('click', dashboard);

if (typeof L === 'undefined' || typeof map === 'undefined' || typeof map.addLayer !== 'function') {
  connection.textContent = 'Карта не загрузилась. Проверьте соединение с источником Leaflet.';
} else {
  const layers = L.layerGroup().addTo(map);
  const markers = new Map(), sectionLayers = new Map();
  let topology, locate, snapshot, socket, retry, role = 'viewer', stopped = false;
  let transportConnected = false;

  async function api(path, method = 'GET') {
    const response = await fetch('/api' + path, { method });
    if (!response.ok) {
      if (response.status === 401) throw new Error('Войдите через вкладку «Диспетчер».');
      const value = await response.json().catch(() => ({}));
      throw new Error(typeof value.detail === 'string' ? value.detail : `Ошибка сервера: ${response.status}`);
    }
    return response.json();
  }

  function controls() {
    const canOperate = transportConnected && role !== 'viewer' && snapshot;
    start.disabled = !canOperate || snapshot.running;
    pause.disabled = !canOperate || !snapshot.running;
  }

  function setConnected(value, message) {
    transportConnected = value;
    panel.dataset.connected = String(value);
    connection.textContent = message;
    controls();
  }

  function draw(state) {
    if (snapshot?.epoch === state.epoch && state.state_version < snapshot.state_version) return;
    snapshot = state;
    panel.dataset.epoch = state.epoch;
    panel.dataset.planId = state.active_plan_id;
    panel.dataset.trainCount = String(state.trains.length);
    panel.dataset.simTime = String(state.sim_time_s);
    const time = new Date((8 * 3600 + state.sim_time_s) * 1000).toISOString().slice(11, 19);
    const status = state.replanning ? 'Расчёт вариантов' : state.awaiting_plan ? 'Ожидание применения плана'
      : state.running ? 'Движение' : 'Пауза';
    clock.textContent = `${time} · ${status} · ${state.trains.length} поездов`;
    metrics.textContent = `Задержки: ${(state.metrics.total_delay_s / 60).toFixed(1)} мин · Энергия: ${state.metrics.energy_kwh.toFixed(1)} кВт·ч`;
    const present = new Set(state.trains.filter(train => train.on_network !== false).map(train => train.id));
    for (const [id, marker] of markers) if (!present.has(id)) { layers.removeLayer(marker); markers.delete(id); }
    for (const train of state.trains) {
      const position = locate(train);
      if (!position) continue;
      const [lon, lat] = position;
      let marker = markers.get(train.id);
      if (!marker) {
        marker = L.circleMarker([lat, lon], { radius: 7, weight: 2, color: '#ffffff', fillOpacity: 1,
          fillColor: train.type === 'passenger' ? '#148976' : '#d09939' }).addTo(layers);
        const label = document.createElement('span');
        label.textContent = train.number || train.id;
        marker.bindTooltip(label, { permanent: true, direction: 'right', className: 'railflow-train-label' });
        markers.set(train.id, marker);
      }
      marker.setLatLng([lat, lon]);
      const lane = trackIndex(train, topology);
      marker.getTooltip().options.direction = lane % 2 ? 'right' : 'left';
      marker.getTooltip().options.offset = L.point(lane % 2 ? 10 : -10, lane % 2 ? 12 : -12);
      marker.getTooltip().update();
      const text = document.createElement('span');
      text.textContent = `${train.number || train.id}: путь ${train.station_track_id || train.main_track_id || '—'}, ${(train.speed_mps * 3.6).toFixed(1)} км/ч, задержка ${(train.delay_s / 60).toFixed(1)} мин. ${train.wait_reason || ''}`;
      marker.bindPopup(text);
    }
    for (const section of state.sections) {
      for (const track of section.tracks || [{id: '1', status: section.status}]) {
        sectionLayers.get(`${section.id}:${track.id}`)?.setStyle({ color: track.status === 'closed' ? '#de6856'
          : track.status === 'signal_failure' ? '#e7a535' : '#253047' });
      }
    }
    controls();
  }

  function fit() {
    if (!topology) return;
    map.fitBounds(topology.stations.map(station => [station.coordinate[1], station.coordinate[0]]), { padding: [50, 50] });
  }
  panel.querySelector('#railflow-fit').addEventListener('click', fit);
  for (const [button, action] of [[start, 'start'], [pause, 'pause']]) {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { draw(await api('/simulation/' + action, 'POST')); }
      catch (error) { connection.textContent = error.message; }
      finally { controls(); }
    });
  }

  async function connect() {
    if (stopped) return;
    clearTimeout(retry);
    try {
      role = (await api('/auth/me')).role;
      if (!topology) {
        topology = await api('/topology');
        locate = createLocator(topology);
        for (const section of topology.sections) {
          for (const track of section.main_tracks || [{id: '1'}]) {
            sectionLayers.set(`${section.id}:${track.id}`, L.polyline((track.geometry || section.geometry).map(([lon, lat]) => [lat, lon]),
              { weight: 2, color: '#253047', opacity: .7, dashArray: '4 3' }).addTo(layers));
          }
        }
      }
      draw(await api('/state'));
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
      socket.onopen = () => setConnected(true, 'Общее состояние с диспетчером · онлайн');
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'state.updated') draw(message.payload);
        } catch (error) { setConnected(false, error.message); socket.close(); }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        setConnected(false, 'Связь потеряна. Показано последнее состояние; переподключение…');
        if (!stopped) retry = setTimeout(connect, 3000);
      };
    } catch (error) {
      setConnected(false, error.message);
      if (!stopped) retry = setTimeout(connect, 5000);
    }
  }
  const resize = new ResizeObserver(() => map.invalidateSize());
  resize.observe(document.getElementById('map'));
  window.addEventListener('pagehide', () => {
    stopped = true;
    clearTimeout(retry);
    resize.disconnect();
    socket?.close();
  });
  connect();
}
