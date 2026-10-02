"""Extract a connected Astana–Kokshetau path from the existing OSM track graph.

Station anchors are approximate demo locations snapped to the graph, not an
import of operational station boundaries. No synthetic track joins are added.
"""
import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def distance(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2-lat1, math.radians(b[0]-a[0])
    return 6371000 * 2 * math.asin(min(1, math.sqrt(math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)))


def main():
    source = json.loads((ROOT/'data/kazakhstan_railways.geojson').read_text(encoding='utf-8'))
    graph, coords = {}, {}
    for feature in source['features']:
        p = feature['properties']
        if p.get('railway') != 'rail' or p.get('service') or p.get('usage') == 'industrial':
            continue
        points, nodes = feature['geometry']['coordinates'], p.get('osm_nodes', [])
        if len(points) != len(nodes):
            continue
        for i in range(len(nodes)-1):
            a, b = points[i:i+2]
            if not all(68 < x[0] < 73 and 50.8 < x[1] < 53.8 for x in (a, b)):
                continue
            u, v = nodes[i:i+2]
            coords[u], coords[v] = a, b
            length = distance(a, b)
            graph.setdefault(u, []).append((v, length))
            graph.setdefault(v, []).append((u, length))
    start = min(coords, key=lambda n: distance(coords[n], [71.409,51.196]))
    end = min(coords, key=lambda n: distance(coords[n], [69.421,53.299]))
    queue, costs, previous = [(0, start)], {start: 0}, {}
    while queue:
        cost, u = heapq.heappop(queue)
        if u == end:
            break
        if cost != costs[u]:
            continue
        for v, length in graph[u]:
            if cost+length < costs.get(v, float('inf')):
                costs[v], previous[v] = cost+length, u
                heapq.heappush(queue, (cost+length, v))
    if end not in costs:
        raise RuntimeError('No connected railway route found between the anchors')
    nodes = [end]
    while nodes[-1] != start:
        nodes.append(previous[nodes[-1]])
    nodes.reverse()
    route = [coords[n] for n in nodes]
    anchors = [('astana','Астана-1',route[0]), ('shortandy','Шортанды',[70.99,51.7]),
               ('akkol','Акколь',[70.94,51.995]), ('makinsk','Макинск',[70.42,52.635]),
               ('burabay','Курорт-Боровое',[70.205,52.935]), ('kokshetau','Кокшетау-1',route[-1])]
    stations = []
    cumulative = [0]
    for a,b in zip(route,route[1:]):
        cumulative.append(cumulative[-1]+distance(a,b))
    for sid, name, anchor in anchors:
        index = min(range(len(route)), key=lambda i: distance(route[i],anchor))
        stations.append({'id':sid,'name':name,'coordinate':route[index], 'position_m':round(cumulative[index],2),
                         'route_index':index,'tracks':8 if sid in ('astana','kokshetau') else 2})
    if any(a['route_index'] >= b['route_index'] for a,b in zip(stations,stations[1:])):
        raise RuntimeError('Station anchors are not ordered on the connected path')
    sections = []
    for i,(a,b) in enumerate(zip(stations,stations[1:])):
        sections.append({'id':f'section-{i}', 'from_station':a['id'], 'to_station':b['id'],
                         'length_m':round(b['position_m']-a['position_m'],2), 'speed_limit_mps':25.0,
                         'geometry':route[a['route_index']:b['route_index']+1], 'status':'open'})
    scenario = {'id':'astana-kokshetau','name':'Астана — Кокшетау','stations':stations,'sections':sections,
                'assumptions':['Demo single-track corridor; actual parallel tracks collapsed into one resource.',
                               'Approximate station anchors snapped to connected OSM railway geometry.',
                               'Uniform 90 km/h limit, level track, no regenerative braking.',
                               'Train parameters, signalling and station capacities are synthetic.'],
                'source':'© OpenStreetMap contributors, ODbL', 'length_m':cumulative[-1]}
    (ROOT/'scenarios').mkdir(exist_ok=True)
    (ROOT/'scenarios/astana-kokshetau.json').write_text(json.dumps(scenario,ensure_ascii=False),encoding='utf-8')
    print(f"Connected corridor: {len(route)} points, {cumulative[-1]/1000:.1f} km")
    print([(s['id'],round(s['position_m']/1000,1)) for s in stations])


if __name__ == '__main__':
    main()
