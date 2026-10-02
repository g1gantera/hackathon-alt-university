<<<<<<< HEAD
# Rail network map (Kazakhstan, OSM 2026-10-01)

| File | What |
|---|---|
| `map.html` + `map_data.js` | Interactive map. Serve the folder (`python3 -m http.server`) and open `/map.html` |
| `network.json` | Full-precision routable track graph for the train simulator |
| `build_network.py` | Rebuilds both data files from an Overpass `out geom` export: `python3 build_network.py railmap.json` |

## network.json schema (`railnet/1`)

- `vertices[]` — `{id, lon, lat, degree, component}`. Degree 1 = dead end / buffer stop, ≥3 = switch or junction.
- `edges[]` — `{id, u, v, way, category, length_m, geometry:[[lon,lat],…]}`. Undirected track segment from vertex `u` to `v`; geometry runs u→v. Edges are split at every switch, way end and station.
- `ways[]` — source OSM way per edge (`osm_id`, `category`, `synthetic`, selected tags: gauge, electrified, voltage, maxspeed, tracks, name, …). `synthetic: true` = short link added by gap healing (negative `osm_id`).
- `stations[]` — `{osm_id, type: station|halt|tram_stop, name, name_en, lon, lat, uic_ref, esr, vertex, snap_dist_m, component}`. `vertex` is the graph vertex on the track where a train stops.
- `suspected_gaps[]` — `{a, b, length_m, name}`. Track ends that face each other but have no track between them in the export. Not routable.
- `stats` — counts, km per category, bounds.

Categories: `main`, `line` (running line with no usage tag), `branch`, `industrial`, `siding`, `yard`, `spur`, `crossover`, `tram`, `subway`, `light_rail`, `narrow_gauge`.

## Notes for running trains

- The graph is undirected and does not encode which way a switch can be taken. Use the angle between consecutive edges (as `route()` in `map.html` does, ≤90° turn) so trains don't reverse through a switch.
- `oneway` / `railway:preferred_direction` are kept in way tags where OSM has them (rare).
- Gap healing joins a track end to another track only when it touches (≤1.5 m) or points at it (≤30 m, ≤35°), or when two main-line ends face each other ≤200 m apart.
=======
# hackathon-alt-university
>>>>>>> origin/era
