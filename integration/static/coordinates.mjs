// Display geometry only. Scheduling and physical motion remain on the server.
const radians = degrees => degrees * Math.PI / 180;

function length(a, b) {
  const lat1 = radians(a[1]), lat2 = radians(b[1]);
  const h = Math.sin((lat2 - lat1) / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(radians(b[0] - a[0]) / 2) ** 2;
  return 12742000 * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function createLocator(topology) {
  const stations = new Map(topology.stations.map(station => [station.id, station]));
  const sections = topology.sections.map(section => {
    function prepare(points) {
      const distances = [0];
      for (let i = 1; i < points.length; i++) distances.push(distances[i - 1] + length(points[i - 1], points[i]));
      return {points, distances};
    }
    return { ...section, ...prepare(section.geometry),
      tracks: new Map((section.main_tracks || []).map(t => [t.id, prepare(t.geometry || section.geometry)])),
      start: stations.get(section.from_station).position_m,
      end: stations.get(section.to_station).position_m };
  });
  return train => {
    if (train.on_network === false) return null;
    if (train.station_id && stations.has(train.station_id)) {
      const station = stations.get(train.station_id);
      return station.track_layout?.find(t => t.id === train.station_track_id)?.coordinate || station.coordinate;
    }
    const position = Math.max(0, Math.min(topology.length_m, train.position_m));
    const section = sections.find(s => s.id === train.section_id)
      || sections.find(s => position >= Math.min(s.start, s.end) && position <= Math.max(s.start, s.end));
    if (!section) throw new Error('Позиция поезда вне геометрии маршрута');
    const fraction = Math.max(0, Math.min(1, (position - section.start) / (section.end - section.start)));
    const line = section.tracks.get(train.main_track_id) || section;
    const target = fraction * line.distances.at(-1);
    let i = line.distances.findIndex(distance => distance >= target);
    const ratio = i > 0 ? (target - line.distances[i - 1]) / (line.distances[i] - line.distances[i - 1] || 1) : 0;
    let point = i <= 0 ? [...line.points[0]] : line.points[i - 1].map((value, axis) => value + ratio * (line.points[i][axis] - value));
    // Blend through the model throat to the actually reserved station track.
    // This is a display connection, not a surveyed switch route.
    const forward = train.direction !== -1;
    for (const [stationId, trackId, distance, endpoint] of [
      [section.from_station, forward ? train.departure_track_id : train.arrival_track_id, position-section.start, line.points[0]],
      [section.to_station, forward ? train.arrival_track_id : train.departure_track_id, section.end-position, line.points.at(-1)]
    ]) {
      const anchor = stations.get(stationId)?.track_layout?.find(t => t.id === trackId)?.coordinate;
      const weight = Math.max(0, 1 - distance / Math.min(250, (section.end-section.start)/2));
      if (anchor && weight) point = point.map((v, axis) => v + weight * (anchor[axis]-endpoint[axis]));
    }
    return point;
  };
}

export function trackIndex(train, topology) {
  const tracks = train.station_id
    ? topology.stations.find(s => s.id === train.station_id)?.track_layout
    : topology.sections.find(s => s.id === train.section_id)?.main_tracks;
  return Math.max(0, (tracks || []).findIndex(t => t.id === (train.station_track_id || train.main_track_id)));
}
