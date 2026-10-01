"""Map contract: WGS84 rail geometry and synthetic train positions on that geometry."""
import math
from bisect import bisect_right
from functools import lru_cache


@lru_cache(maxsize=64)
def _distances(points):
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
        value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        distances.append(distances[-1] + 12742000 * math.asin(min(1, math.sqrt(value))))
    return tuple(distances)


def locate(topology, position_m, direction=1):
    """Project route chainage to a rail segment; bearing is clockwise from north.

    Each section's model length is mapped proportionally to its OSM polyline.
    Never interpolate straight across stations or offset the physical marker.
    """
    if not math.isfinite(position_m):
        raise ValueError('Train position must be finite')
    position = max(0, min(topology['length_m'], position_m))
    stations = {s['id']: s for s in topology['stations']}
    sections = topology['sections']
    section = next((s for s in sections if position < stations[s['to_station']]['position_m']), sections[-1])
    start = stations[section['from_station']]['position_m']
    fraction = max(0, min(1, (position-start)/section['length_m']))
    points = tuple(tuple(p) for p in section['geometry'])
    distances = _distances(points)
    if len(points)<2 or distances[-1]<=0:
        raise ValueError('Rail section must contain a nonzero polyline')
    target = fraction * distances[-1]
    index = min(len(points)-2, max(0, bisect_right(distances, target)-1))
    while index>0 and distances[index+1]==distances[index]:
        index -= 1
    ratio = (target-distances[index])/(distances[index+1]-distances[index])
    a,b = points[index:index+2]
    coordinate = [a[i]+ratio*(b[i]-a[i]) for i in (0,1)]
    lon1,lat1,lon2,lat2 = map(math.radians, (*a,*b))
    bearing = math.degrees(math.atan2(math.sin(lon2-lon1)*math.cos(lat2),
        math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(lon2-lon1)))
    return {'coordinate':coordinate, 'bearing_deg':(bearing+(180 if direction<0 else 0))%360}


def train_map_data(topology, train):
    route = topology['stations'] if train['direction']>0 else list(reversed(topology['stations']))
    return {**locate(topology, train['position_m'], train['direction']),
            'origin_id':route[0]['id'], 'destination_id':route[-1]['id'],
            'route_station_ids':[s['id'] for s in route],
            'route_name':f"{route[0]['name']} → {route[-1]['name']}",
            'position_source':'simulation'}


def infrastructure(topology):
    features = []
    for station in topology['stations']:
        features.append({'type':'Feature','id':station['id'],
            'properties':{'kind':'station','id':station['id'],'name':station['name'],
                          'tracks':station['tracks'],'position_m':station['position_m'],
                          'operating_data':'demo_assumption'},
            'geometry':{'type':'Point','coordinates':station['coordinate']}})
    for section in topology['sections']:
        features.append({'type':'Feature','id':section['id'],
            'properties':{'kind':'railway','id':section['id'],'from_station':section['from_station'],
                          'to_station':section['to_station'],'length_m':section['length_m']},
            'geometry':{'type':'LineString','coordinates':section['geometry']}})
    return {'type':'FeatureCollection','features':features,
            'metadata':{'schema_version':1,'coordinate_order':'longitude, latitude',
                        'geometry_source':'OpenStreetMap','train_data':'simulation',
                        'network_url':'/api/network'}}
