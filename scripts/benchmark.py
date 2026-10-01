"""Repeatable backend measurement; uses only the supplied network and in-memory journal."""
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.engine import Engine
from backend.models import IncidentSpec
from backend.network import Network

net=Network()
results={'platform':platform.platform(),'python':platform.python_version(),'scenarios':{},'note':'Local measurements, not production or certified safety benchmarks.'}
for scenario in ('passing','overtaking','priority','closure'):
    e=Engine(net);e.load_demo(scenario)
    start=time.perf_counter()
    errors=[]
    for _ in range(700):
        e.advance(5)
        errors.extend(e.invariant_errors())
    results['scenarios'][scenario]={'simulation_seconds':e.sim_time,'wall_seconds':round(time.perf_counter()-start,4),'constraint_errors':len(errors),'arrivals':{t.spec.id:t.arrivals for t in e.trains.values()},'states':{t.spec.id:t.state for t in e.trains.values()},'quality':e.quality.result(e)}
times=[]
for trial in range(20):
    e=Engine(net);e.load_demo('passing');e.advance(40)
    specs=[IncidentSpec(id=f'B{i}',kind='train_breakdown' if i%2 else 'train_delay',asset_type='train',asset_id='KZ-101',duration_s=30+i) for i in range(10)]
    start=time.perf_counter();e.add_incidents(specs);times.append((time.perf_counter()-start)*1000)
results['ten_incident_batch_ms']={'trials':len(times),'median':round(statistics.median(times),3),'p95':round(sorted(times)[18],3),'max':round(max(times),3)}
out=Path(__file__).resolve().parents[1]/'artifacts/backend-results.json'
out.write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(results,indent=2,ensure_ascii=False))
