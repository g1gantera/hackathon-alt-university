#!/usr/bin/env python3
"""Turn an Overpass `out geom` railway dump into a routable track graph + map data.

Usage:  python3 build_network.py /path/to/railmap.json

Outputs (next to this script):
  network.json      full-precision graph for the train simulator
  map_data.js       compact copy of the same graph that map.html loads
"""
import json, math, sys, os
from collections import defaultdict, Counter

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Downloads/railmap.json")
OUT = os.path.dirname(os.path.abspath(__file__))

SNAP_MAIN_M = 800    # station → running line search radius
SNAP_ANY_M = 1500    # fallback: any track

# ---------------------------------------------------------------- helpers
R = 6371008.8
def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def category(t):
    rw = t.get("railway")
    if rw in ("tram", "subway", "light_rail", "narrow_gauge"):
        # depot/yard tracks of urban systems still get their own class
        return rw if t.get("service") not in ("yard", "siding", "spur", "crossover") else t["service"]
    svc = t.get("service")
    if svc in ("siding", "yard", "spur", "crossover"):
        return svc
    u = t.get("usage")
    if u == "main": return "main"
    if u == "branch": return "branch"
    if u in ("industrial", "military", "service", "tourism"): return "industrial"
    return "line"   # untagged running line

RUNNING = {"main", "branch", "line", "industrial", "narrow_gauge"}

def num(v):
    try: return float(str(v).split(";")[0])
    except Exception: return None

# ---------------------------------------------------------------- load
els = json.load(open(SRC))["elements"]
ways = [e for e in els if e["type"] == "way" and len(e.get("geometry", [])) >= 2]
pois = [e for e in els if e["type"] == "node"]
print(f"{len(ways)} ways, {len(pois)} station/stop nodes")

# ---------------------------------------------------------------- vertices
# Overpass geom has no node ids; OSM shared nodes produce identical coordinates,
# so a coordinate key reproduces topology exactly.
key = lambda p: (round(p["lon"], 7), round(p["lat"], 7))

# ---------------------------------------------------------------- gap healing
# OSM has many track ends that stop a few metres short of (or just past) the
# track they obviously join. Connect a dead end to the nearest point on another
# track when it is either practically touching it, or pointing straight at it.
HEAL_TOUCH_M, HEAL_MAX_M, HEAL_ANGLE = 1.5, 30.0, 35.0
def heal_gaps():
    cnt = Counter()
    for w in ways:
        for k in {key(p) for p in w["geometry"]}: cnt[k] += 1
    def xy(k, lat0):  # local metres
        return (k[0] * 111320 * math.cos(math.radians(lat0)), k[1] * 110540)
    C = 0.003
    segs = defaultdict(list)
    for wi, w in enumerate(ways):
        g = [key(p) for p in w["geometry"]]
        for si in range(len(g) - 1):
            a, b = g[si], g[si + 1]
            for cx in range(int(min(a[0], b[0]) / C) - 1, int(max(a[0], b[0]) / C) + 2):
                for cy in range(int(min(a[1], b[1]) / C) - 1, int(max(a[1], b[1]) / C) + 2):
                    segs[(cx, cy)].append((wi, si))
    inserts = defaultdict(list)   # (wi, si) -> [(t, key)]
    links = []
    for wi, w in enumerate(ways):
        g = [key(p) for p in w["geometry"]]
        for end, inner in ((g[0], g[1]), (g[-1], g[-2])):
            if cnt[end] != 1: continue
            lat0 = end[1]; E0 = xy(end, lat0); I0 = xy(inner, lat0)
            out = (E0[0] - I0[0], E0[1] - I0[1]); on = math.hypot(*out) or 1
            best = None
            seen = set()
            for cand in segs.get((int(end[0] / C), int(end[1] / C)), ()):
                if cand in seen or cand[0] == wi: continue
                seen.add(cand)
                g2 = ways[cand[0]]["geometry"]
                A = xy(key(g2[cand[1]]), lat0); B = xy(key(g2[cand[1] + 1]), lat0)
                dx, dy = B[0] - A[0], B[1] - A[1]; L2 = dx * dx + dy * dy
                t = 0 if L2 == 0 else max(0, min(1, ((E0[0] - A[0]) * dx + (E0[1] - A[1]) * dy) / L2))
                P = (A[0] + t * dx, A[1] + t * dy)
                d = math.hypot(P[0] - E0[0], P[1] - E0[1])
                if d > HEAL_MAX_M: continue
                if d > HEAL_TOUCH_M:
                    cosang = ((P[0] - E0[0]) * out[0] + (P[1] - E0[1]) * out[1]) / (d * on)
                    if cosang < math.cos(math.radians(HEAL_ANGLE)): continue
                if best is None or d < best[0]: best = (d, cand, t)
            if best is None: continue
            d, (wj, sj), t = best
            g2 = ways[wj]["geometry"]
            A, B = key(g2[sj]), key(g2[sj + 1])
            if t < 1e-3: P = A
            elif t > 1 - 1e-3: P = B
            else:
                P = (round(A[0] + t * (B[0] - A[0]), 7), round(A[1] + t * (B[1] - A[1]), 7))
                inserts[(wj, sj)].append((t, P))
            links.append((wi, end, P, d))
    for (wj, sj), pts in sorted(inserts.items(), key=lambda x: (x[0][0], -x[0][1])):
        g = ways[wj]["geometry"]
        for t, P in sorted(pts, reverse=True):
            g.insert(sj + 1, {"lon": P[0], "lat": P[1]})
    for wi, end, P, d in links:
        if end == P: continue
        src = ways[wi]
        ways.append({"type": "way", "id": -len(ways), "synthetic": True,
                     "tags": dict(src["tags"], **{"note": f"synthetic gap link {d:.1f} m (healed)"}),
                     "geometry": [{"lon": end[0], "lat": end[1]}, {"lon": P[0], "lat": P[1]}]})
    return len(links), sum(1 for l in links if l[3] <= HEAL_TOUCH_M)

# Second pass: two running-line ends facing each other across a longer gap
# (missing piece of main line). Both must point at each other.
PAIR_MAX_M, PAIR_ANGLE = 200.0, 30.0
def heal_pairs():
    cnt = Counter()
    for w in ways:
        for k in {key(p) for p in w["geometry"]}: cnt[k] += 1
    ends = []
    for wi, w in enumerate(ways):
        if category(w["tags"]) not in ("main", "line", "branch"): continue
        g = [key(p) for p in w["geometry"]]
        for end, inner in ((g[0], g[1]), (g[-1], g[-2])):
            if cnt[end] == 1: ends.append((wi, end, inner))
    def xy(k, lat0): return (k[0] * 111320 * math.cos(math.radians(lat0)), k[1] * 110540)
    cands = []
    cosmax = math.cos(math.radians(PAIR_ANGLE))
    for i in range(len(ends)):
        wi, a, ai = ends[i]
        for j in range(i + 1, len(ends)):
            wj, b, bi = ends[j]
            if abs(a[0] - b[0]) > 0.003 or abs(a[1] - b[1]) > 0.002: continue
            lat0 = a[1]; A, AI, B, BI = xy(a, lat0), xy(ai, lat0), xy(b, lat0), xy(bi, lat0)
            d = math.hypot(B[0] - A[0], B[1] - A[1])
            if d > PAIR_MAX_M or d == 0: continue
            def ok(P, PI, T):
                o = (P[0] - PI[0], P[1] - PI[1]); on = math.hypot(*o) or 1
                return ((T[0] - P[0]) * o[0] + (T[1] - P[1]) * o[1]) / (d * on) >= cosmax
            if ok(A, AI, B) and ok(B, BI, A): cands.append((d, i, j))
    used, n = set(), 0
    for d, i, j in sorted(cands):
        if i in used or j in used: continue
        used |= {i, j}; n += 1
        (wi, a, _), (_, b, _) = ends[i], ends[j]
        ways.append({"type": "way", "id": -len(ways), "synthetic": True,
                     "tags": dict(ways[wi]["tags"], **{"note": f"synthetic gap link {d:.1f} m (healed end-to-end)"}),
                     "geometry": [{"lon": a[0], "lat": a[1]}, {"lon": b[0], "lat": b[1]}]})
    return n
healed = heal_gaps()
healed = (healed[0] + heal_pairs(), healed[1])
print(f"healed {healed[0]} gaps ({healed[1]} touching, rest aligned ≤{HEAL_MAX_M:.0f} m)")

use = Counter()
for w in ways:
    seen = set()
    for p in w["geometry"]:
        k = key(p)
        if k not in seen:
            use[k] += 1; seen.add(k)
split = set(k for k, c in use.items() if c > 1)
for w in ways:
    split.add(key(w["geometry"][0])); split.add(key(w["geometry"][-1]))

# ---------------------------------------------------------------- station snapping
# Snap each station to the nearest geometry point of a running line and force a
# graph vertex there, so trains can route station → station.
cell = 0.02
grid = defaultdict(list)          # (cx,cy) -> [(k, cat)]
for w in ways:
    c = category(w["tags"])
    for p in w["geometry"]:
        k = key(p)
        grid[(int(k[0] / cell), int(k[1] / cell))].append((k, c))

def nearest(lon, lat, radius, pred):
    r = int(radius / 1500) + 1
    cx, cy = int(lon / cell), int(lat / cell)
    best, bd = None, radius
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for k, c in grid.get((cx + dx, cy + dy), ()):
                if not pred(c): continue
                d = hav((lon, lat), k)
                if d < bd: best, bd = k, d
    return best, bd

stations = []
for n in pois:
    t = n["tags"]; rw = t.get("railway")
    if rw == "tram_stop":
        k, d = nearest(n["lon"], n["lat"], 300, lambda c: True)
    else:
        k, d = nearest(n["lon"], n["lat"], SNAP_MAIN_M, lambda c: c in RUNNING or c in ("subway", "light_rail", "tram"))
        if k is None:
            k, d = nearest(n["lon"], n["lat"], SNAP_ANY_M, lambda c: True)
    if k is not None: split.add(k)
    stations.append(dict(
        osm_id=n["id"], type=rw,
        name=t.get("name") or t.get("name:ru") or t.get("int_name") or "",
        name_en=t.get("name:en") or t.get("int_name") or t.get("uic_name") or "",
        lon=n["lon"], lat=n["lat"], uic_ref=t.get("uic_ref"), esr=t.get("esr:user"),
        operator=t.get("operator"), _snap=k, snap_dist_m=round(d, 1) if k else None))

# ---------------------------------------------------------------- edges
vid = {}
verts = []
def V(k):
    if k not in vid:
        vid[k] = len(verts); verts.append(k)
    return vid[k]

KEEP = ["railway", "usage", "service", "name", "ref", "gauge", "electrified", "voltage",
        "frequency", "maxspeed", "tracks", "passenger_lines", "operator", "bridge", "tunnel",
        "layer", "oneway", "railway:preferred_direction", "railway:track_ref", "branch"]
waylist, edges = [], []
for w in ways:
    t = w["tags"]; cat = category(t)
    wi = len(waylist)
    waylist.append(dict(osm_id=w["id"], category=cat, synthetic=w.get("synthetic", False),
                        tags={k: t[k] for k in KEEP + (["note"] if w.get("synthetic") else []) if k in t}))
    g = [key(p) for p in w["geometry"]]
    seg = [g[0]]
    for k in g[1:]:
        if k == seg[-1]: continue
        seg.append(k)
        if k in split:
            L = sum(hav(seg[i], seg[i + 1]) for i in range(len(seg) - 1))
            edges.append(dict(u=V(seg[0]), v=V(seg[-1]), way=wi, cat=cat, len=round(L, 2), coords=seg))
            seg = [k]

deg = Counter()
for e in edges:
    deg[e["u"]] += 1; deg[e["v"]] += 1

# connected components (union-find)
par = list(range(len(verts)))
def find(x):
    while par[x] != x:
        par[x] = par[par[x]]; x = par[x]
    return x
for e in edges:
    a, b = find(e["u"]), find(e["v"])
    if a != b: par[a] = b
comp = [find(i) for i in range(len(verts))]
csize = Counter(comp)
crank = {c: i for i, (c, _) in enumerate(csize.most_common())}

for s in stations:
    s["vertex"] = vid.get(s.pop("_snap")) if s.get("_snap") is not None else None
    s.pop("_snap", None)
    s["component"] = crank[comp[s["vertex"]]] if s["vertex"] is not None else None

# ---------------------------------------------------------------- suspected gaps (not healed)
# Pairs of running-line dead ends that face each other 200 m – 15 km apart:
# track is very likely missing from the source export. Reported, never invented.
inc = defaultdict(list)
for i, e in enumerate(edges):
    inc[e["u"]].append(i); inc[e["v"]].append(i)
def outdir(vx):
    e = edges[inc[vx][0]]; c = e["coords"]
    p, q = (c[-1], c[-2]) if e["v"] == vx else (c[0], c[1])
    k = math.cos(math.radians(p[1]))
    return ((p[0] - q[0]) * k, p[1] - q[1])
runends = [i for i in range(len(verts)) if deg[i] == 1 and edges[inc[i][0]]["cat"] in ("main", "line", "branch")]
gc = []
for ai, a in enumerate(runends):
    for b in runends[ai + 1:]:
        A, B = verts[a], verts[b]
        if abs(A[0] - B[0]) > 0.2 or abs(A[1] - B[1]) > 0.14: continue
        d = hav(A, B)
        if not 200 < d < 15000: continue
        k = math.cos(math.radians(A[1])); ab = ((B[0] - A[0]) * k, B[1] - A[1]); n_ab = math.hypot(*ab)
        oa, ob = outdir(a), outdir(b)
        ca = (oa[0] * ab[0] + oa[1] * ab[1]) / (math.hypot(*oa) * n_ab or 1)
        cb = -(ob[0] * ab[0] + ob[1] * ab[1]) / (math.hypot(*ob) * n_ab or 1)
        if ca > 0.7 and cb > 0.7: gc.append((d, a, b))
gaps, usedv = [], set()
for d, a, b in sorted(gc):
    if a in usedv or b in usedv: continue
    usedv |= {a, b}
    gaps.append(dict(a=a, b=b, length_m=round(d), category=edges[inc[a][0]]["cat"],
                     name=waylist[edges[inc[a][0]]["way"]]["tags"].get("name") or waylist[edges[inc[b][0]]["way"]]["tags"].get("name")))
print(f"{len(gaps)} suspected missing sections, e.g.", sorted(gaps, key=lambda g: -g["length_m"])[:3])

stats = dict(
    ways=len(ways), healed_gaps=healed[0], suspected_gaps=len(gaps), edges=len(edges), vertices=len(verts),
    junctions=sum(1 for d in deg.values() if d >= 3),
    dead_ends=sum(1 for d in deg.values() if d == 1),
    components=len(csize), largest_component_vertices=csize.most_common(1)[0][1],
    track_km_by_category={c: round(sum(e["len"] for e in edges if e["cat"] == c) / 1000, 1)
                          for c in sorted(set(e["cat"] for e in edges))},
    stations=Counter(s["type"] for s in stations),
    stations_unsnapped=sum(1 for s in stations if s["vertex"] is None),
    bounds=[min(v[0] for v in verts), min(v[1] for v in verts), max(v[0] for v in verts), max(v[1] for v in verts)],
    source=SRC.split("/")[-1], osm_timestamp=json.load(open(SRC))["osm3s"]["timestamp_osm_base"],
)
stats["track_km_total"] = round(sum(stats["track_km_by_category"].values()), 1)

# ---------------------------------------------------------------- network.json (full precision)
net = dict(
    schema="railnet/1",
    attribution="© OpenStreetMap contributors, ODbL",
    stats=stats,
    vertices=[dict(id=i, lon=k[0], lat=k[1], degree=deg[i], component=crank[comp[i]]) for i, k in enumerate(verts)],
    edges=[dict(id=i, u=e["u"], v=e["v"], way=e["way"], category=e["cat"], length_m=e["len"],
                geometry=[[k[0], k[1]] for k in e["coords"]]) for i, e in enumerate(edges)],
    ways=waylist,
    stations=stations,
    suspected_gaps=gaps,
)
with open(os.path.join(OUT, "network.json"), "w") as f:
    json.dump(net, f, ensure_ascii=False, separators=(",", ":"))

# ---------------------------------------------------------------- map_data.js (compact)
Q = 1e5  # ~1 m
CATS = sorted(set(e["cat"] for e in edges))
def enc(coords):
    out, px, py = [], 0, 0
    for lon, lat in coords:
        x, y = round(lon * Q), round(lat * Q)
        out += [x - px, y - py]; px, py = x, y
    return out
compact = dict(
    Q=Q, cats=CATS, stats=stats,
    v=[[round(k[0] * Q), round(k[1] * Q)] for k in verts],
    e=[[e["u"], e["v"], CATS.index(e["cat"]), e["way"], round(e["len"]), enc(e["coords"])] for e in edges],
    w=[[w["osm_id"], w["tags"]] for w in waylist],
    s=[[s["osm_id"], s["type"], s["name"], s["name_en"], round(s["lon"] * Q), round(s["lat"] * Q),
        s["vertex"], s["uic_ref"], s["snap_dist_m"]] for s in stations],
    g=[[g["a"], g["b"], g["length_m"], g["name"]] for g in gaps],
)
with open(os.path.join(OUT, "map_data.js"), "w") as f:
    f.write("window.RAIL=")
    json.dump(compact, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";\n")

print(json.dumps(stats, ensure_ascii=False, indent=1))
for fn in ("network.json", "map_data.js"):
    print(fn, round(os.path.getsize(os.path.join(OUT, fn)) / 1e6, 2), "MB")
