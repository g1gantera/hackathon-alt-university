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
    const points = section.geometry;
    const distances = [0];
    for (let i = 1; i < points.length; i++) distances.push(distances[i - 1] + length(points[i - 1], points[i]));
    return { ...section, points, distances, start: stations.get(section.from_station).position_m,
      end: stations.get(section.to_station).position_m };
  });
  return train => {
    if (train.station_id && stations.has(train.station_id)) return stations.get(train.station_id).coordinate;
    const position = Math.max(0, Math.min(topology.length_m, train.position_m));
    const section = sections.find(s => s.id === train.section_id)
      || sections.find(s => position >= Math.min(s.start, s.end) && position <= Math.max(s.start, s.end));
    if (!section) throw new Error('Позиция поезда вне геометрии маршрута');
    const fraction = Math.max(0, Math.min(1, (position - section.start) / (section.end - section.start)));
    const target = fraction * section.distances.at(-1);
    let i = section.distances.findIndex(distance => distance >= target);
    if (i <= 0) return section.points[0];
    const ratio = (target - section.distances[i - 1]) / (section.distances[i] - section.distances[i - 1] || 1);
    return section.points[i - 1].map((value, axis) => value + ratio * (section.points[i][axis] - value));
  };
}
