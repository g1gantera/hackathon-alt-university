"""Level-track traction model. SI units; energy is positive traction work / eta."""
import math
from functools import lru_cache
import numpy as np


@lru_cache(maxsize=256)
def profile(length: float, limit: float, mass: float, passenger: bool, duration: int = 0):
    acceleration, brake, efficiency = (0.45, 0.65, 0.88) if passenger else (0.2, 0.4, 0.85)
    x = np.linspace(0, length, max(3, math.ceil(length / 100) + 1))

    def calculate(cruise):
        v = np.full(len(x), cruise)
        v[-1] = 0
        for i in range(len(x)-2, -1, -1):
            v[i] = min(v[i], math.sqrt(v[i+1]**2 + 2*brake*(x[i+1]-x[i])))
        v[0] = 0
        for i in range(1, len(x)):
            v[i] = min(v[i], math.sqrt(v[i-1]**2 + 2*acceleration*(x[i]-x[i-1])))
        dt = 2*np.diff(x)/np.maximum(v[1:]+v[:-1], 1e-6)
        times = np.concatenate(([0], np.cumsum(dt)))
        return times, v

    t, v = calculate(limit)
    minimum = math.ceil(float(t[-1]))
    target = duration or minimum
    if target < minimum:
        return {'reachable':False, 'minimum_time_s':minimum, 'points':[]}
    low, high = 0.01, limit
    for _ in range(45):
        mid = (low+high)/2
        t, v = calculate(mid)
        if t[-1] > target:
            low = mid
        else:
            high = mid
    t, v = calculate(high)
    # Resistance: rolling coefficient plus quadratic aerodynamic proxy.
    resistance = mass*9.81*0.0015 + 3.0*((v[1:]+v[:-1])/2)**2
    work = np.maximum(0, 0.5*mass*np.diff(v*v) + resistance*np.diff(x)) / efficiency
    energy = np.concatenate(([0], np.cumsum(work)/3.6e6))
    return {'reachable':True, 'minimum_time_s':minimum, 'duration_s':target,
            'energy_kwh':float(energy[-1]), 'limit_mps':limit,
            'points':[[float(a),float(b),float(c),float(d)] for a,b,c,d in zip(t,x,v,energy)],
            'assumptions':'Level track; no regeneration; synthetic resistance and traction parameters.'}


def sample(p, seconds):
    points = np.asarray(p['points'])
    index = min(len(points)-2, max(0, int(np.searchsorted(points[:,0], seconds, side='right'))-1))
    a,b = points[index:index+2]
    dt = max(0, min(seconds-a[0], b[0]-a[0]))
    acceleration = (b[2]-a[2])/(b[0]-a[0])
    return (float(min(points[-1,1],max(0,a[1]+a[2]*dt+0.5*acceleration*dt*dt))), float(max(0,a[2]+acceleration*dt)),
            float(np.interp(seconds,points[:,0],points[:,3])))
