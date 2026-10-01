"""Read-only access to the supplied graph. Never infer connections from crossings."""
import bisect
import hashlib
import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def hav(a, b):
    p, q = math.radians(a[1]), math.radians(b[1])
    h = math.sin((q-p)/2)**2 + math.cos(p)*math.cos(q)*math.sin(math.radians(b[0]-a[0])/2)**2
    return 12742017.6 * math.asin(min(1, math.sqrt(h)))


class Network:
    def __init__(self, path=ROOT / 'network.json'):
        raw = json.loads(Path(path).read_text())
        self.vertices = raw['vertices']
        self.edges = raw['edges']
        self.ways = raw['ways']
        self.stations = raw['stations']
        self.stats = raw['stats']
        self.adj = [[] for _ in self.vertices]
        self.allowed = set()
        self.arc_lengths = {}
        for e in self.edges:
            self.adj[e['u']].append(e['id'])
            self.adj[e['v']].append(e['id'])
            if not self.ways[e['way']].get('synthetic') and e['category'] not in {'tram', 'subway', 'light_rail', 'narrow_gauge'} and e['length_m'] > 0:
                self.allowed.add(e['id'])
        self.station_at = {}
        for s in self.stations:
            if s['vertex'] is not None and s['type'] != 'tram_stop':
                self.station_at.setdefault(s['vertex'], s)
        self.hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('map.html','map_data.js','network.json','build_network.py')}

    def label(self, vertex):
        s = self.station_at.get(vertex)
        return (s.get('name_en') or s.get('name')) if s and s.get('name') else f'Track point V{vertex}'

    def endpoint(self, arc):
        e = self.edges[arc[0]]
        return e['v'] if arc[1] == 0 else e['u']

    def startpoint(self, arc):
        e = self.edges[arc[0]]
        return e['u'] if arc[1] == 0 else e['v']

    def heading(self, arc, end=False):
        p = self.edges[arc[0]]['geometry']
        a, b = (p[-2], p[-1]) if end else (p[0], p[1])
        if arc[1]:
            a, b = (p[1], p[0]) if end else (p[-1], p[-2])
        return math.atan2((b[0]-a[0])*math.cos(math.radians(a[1])), b[1]-a[1])

    def compatible(self, previous, arc):
        if previous is None:
            return True
        if previous[0] == arc[0] or self.endpoint(previous) != self.startpoint(arc):
            return False
        delta = abs(self.heading(previous, True)-self.heading(arc))
        return min(delta, 2*math.pi-delta) <= math.pi/2 + 1e-6

    def direction_allowed(self, arc):
        tags = self.ways[self.edges[arc[0]]['way']]['tags']
        oneway = str(tags.get('oneway', 'no')).lower()
        return not (oneway in {'yes', '1', 'true'} and arc[1] == 1 or oneway == '-1' and arc[1] == 0)

    def route(self, src, dst, blocked_edges=frozenset(), blocked_vertices=frozenset(), incoming=None, limit_m=2_000_000):
        if src == dst:
            return []
        if dst in blocked_vertices:
            return None
        heap = [(0, -1, -1)]
        distances = {(-1, -1): 0}
        previous = {}
        visited = 0
        while heap:
            cost, eid, direction = heapq.heappop(heap)
            state = (eid, direction)
            if cost != distances.get(state):
                continue
            at = src if eid == -1 else self.endpoint(state)
            if at == dst:
                result = []
                while state != (-1, -1):
                    result.append(state)
                    state = previous[state]
                return result[::-1]
            if cost > limit_m:
                break
            visited += 1
            if visited > 110000:
                return None
            inc = incoming if eid == -1 else state
            for next_id in self.adj[at]:
                if next_id not in self.allowed or next_id in blocked_edges:
                    continue
                e = self.edges[next_id]
                arc = (next_id, 0 if e['u'] == at else 1)
                if self.endpoint(arc) in blocked_vertices or not self.compatible(inc, arc) or not self.direction_allowed(arc):
                    continue
                new_cost = cost + e['length_m']
                if new_cost < distances.get(arc, math.inf):
                    distances[arc] = new_cost
                    previous[arc] = state
                    heapq.heappush(heap, (new_cost, *arc))
        return None

    def lengths(self, route):
        # Routes are replaced or extended, never edited in place, so identity
        # plus length identifies a cached prefix-sum. Callers must not mutate it.
        cache = self.__dict__.setdefault('_length_cache', {})
        hit = cache.get(id(route))
        if hit is not None and hit[0] is route and hit[1] == len(route):
            return hit[2]
        values = [0.0]
        for eid, _ in route:
            values.append(values[-1] + self.edges[eid]['length_m'])
        if len(cache) >= 4096:
            cache.clear()
        cache[id(route)] = (route, len(route), values)
        return values

    def locate(self, route, distance):
        lengths = self.lengths(route)
        i = min(len(route)-1, max(0, bisect.bisect_right(lengths, distance)-1))
        arc = route[i]
        offset = max(0, min(self.edges[arc[0]]['length_m'], distance-lengths[i]))
        return i, arc, offset

    def position(self, arc, offset):
        eid, direction = arc
        e = self.edges[eid]
        points = e['geometry']
        if eid not in self.arc_lengths:
            lengths = [0.0]
            for a, b in zip(points, points[1:]):
                lengths.append(lengths[-1]+hav(a,b))
            self.arc_lengths[eid] = lengths
        lengths = self.arc_lengths[eid]
        fraction = offset / e['length_m']
        d = (1-fraction if direction else fraction)*lengths[-1]
        i = min(len(points)-2, max(0, bisect.bisect_right(lengths,d)-1))
        f = (d-lengths[i])/max(1e-9,lengths[i+1]-lengths[i])
        a,b = points[i],points[i+1]
        return [a[1]+(b[1]-a[1])*f, a[0]+(b[0]-a[0])*f]

    def speed_limit(self, eid):
        # Tags are immutable after loading; ATO and signals query this per block.
        cache = self.__dict__.setdefault('speed_limits', {})
        cached = cache.get(eid)
        if cached is not None:
            return cached
        e = self.edges[eid]
        tag = str(self.ways[e['way']]['tags'].get('maxspeed',''))
        try:
            value = float(tag.replace('mph','').split(';')[0])
            limit = max(5,min(160, value*(1.609344 if 'mph' in tag else 1)))
        except ValueError:
            limit = 40 if e['category'] in {'siding','yard','crossover','spur'} else 80
        cache[eid] = limit
        return limit

    def find_demo(self):
        """Discover a real loop and its approaches; no new nodes or edges."""
        candidates = sorted((e for e in self.edges if e['id'] in self.allowed and e['category']=='siding' and 450 < e['length_m'] < 2200), key=lambda e: (abs(e['length_m']-1000),e['id']))
        for e in candidates:
            bypass = self.route(e['u'],e['v'],{e['id']},limit_m=6000)
            if not bypass or self.lengths(bypass)[-1] > 4500:
                continue
            for ai in self.adj[e['u']]:
                ae = self.edges[ai]
                incoming = (ai, 0 if ae['v']==e['u'] else 1)
                if ai not in self.allowed or ae['length_m']<300 or not self.compatible(incoming,(e['id'],0)) or not self.compatible(incoming,bypass[0]):
                    continue
                for bi in self.adj[e['v']]:
                    be = self.edges[bi]
                    outgoing=(bi,0 if be['u']==e['v'] else 1)
                    if bi not in self.allowed or be['length_m']<300 or not self.compatible((e['id'],0),outgoing) or not self.compatible(bypass[-1],outgoing):
                        continue
                    if ai==bi or ai in [a[0] for a in bypass] or bi in [a[0] for a in bypass]:
                        continue
                    return {'origin':self.startpoint(incoming),'destination':self.endpoint(outgoing),'siding_edge':e['id'],'siding_length_m':e['length_m'],'siding_route':[incoming,(e['id'],0),outgoing],'bypass_route':[incoming,*bypass,outgoing], 'description':'Existing mapped siding; usable length assumes 25 m clearance at each end. No verified platform inventory.'}
        raise RuntimeError('No verified loop candidate found; do not fabricate demonstration infrastructure')
