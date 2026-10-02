import json
import math
from pathlib import Path
from .demo_advisory import profile

ROOT = Path(__file__).resolve().parents[2]
DWELL = 90
SWITCH_TIME = 15
HORIZON = 172800


def topology():
    value = json.loads((ROOT/'scenarios/astana-kokshetau.json').read_text(encoding='utf-8'))
    value['signals'] = [{'id':f'signal-{i}', 'resource':s['id'], 'state':'green'} for i,s in enumerate(value['sections'])]
    value['switches'] = [{'id':f'switch-{s["id"]}', 'station_id':s['id'], 'position':'normal', 'available':True} for s in value['stations']]
    return value


def trains():
    result = []
    for i in range(8):
        passenger = i % 4 < 2
        result.append({'id':f'T{i+1:02}', 'number':str(101+i) if passenger else str(2001+i),
                       'type':'passenger' if passenger else 'freight', 'priority':3 if passenger else 1,
                       'length_m':350 if passenger else 850, 'mass_kg':650000 if passenger else 4200000,
                       'max_speed_mps':25 if passenger else 20, 'direction':1 if i%2==0 else -1,
                       'ready_s':(i//2)*600, 'route':list(range(5)) if i%2==0 else list(reversed(range(5)))})
    return result


def movement_profile(train, section, duration=0):
    return profile(section['length_m'], min(section['speed_limit_mps'],train['max_speed_mps']),
                   train['mass_kg'], train['type']=='passenger', duration)


def clearance(train):
    # Conservative tail-release at 5 m/s plus a signalling margin.
    return math.ceil(train['length_m']/5)+15


def started(state, movement):
    if 'committed' in state:
        return [movement['train_id'],movement['leg']] in state['committed']
    return movement['start_s'] < state['sim_time_s']


def stops(train):
    return list(range(6)) if train['direction']==1 else list(reversed(range(6)))


def station_intervals(topo, fleet, movements):
    intervals = []
    for train in fleet:
        legs = sorted([m for m in movements if m['train_id']==train['id']], key=lambda m:m['leg'])
        if len(legs)!=5:
            continue
        route = stops(train)
        for i, station in enumerate(route):
            start = 0 if i==0 else legs[i-1]['end_s']
            end = HORIZON if i==5 else legs[i]['start_s']+SWITCH_TIME
            intervals.append({'station_index':station,'start_s':start,'end_s':end,'train_id':train['id']})
    return intervals


def assign_tracks(topo, fleet, movements):
    occupancy = station_intervals(topo,fleet,movements)
    for si, station in enumerate(topo['stations']):
        free_at = [0]*station['tracks']
        for item in sorted([x for x in occupancy if x['station_index']==si],key=lambda x:x['start_s']):
            track = min(range(len(free_at)),key=lambda k:free_at[k])
            if free_at[track] > item['start_s']:
                return None
            free_at[track] = item['end_s']
            item['track'] = track+1
    return occupancy
