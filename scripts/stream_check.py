"""Read-only measurement of SSE cadence and full-state resynchronization."""
import json
import os
import statistics
import time
from pathlib import Path
import httpx

base=os.getenv('RAIL_TEST_URL','http://127.0.0.1:8000')
auth=('dispatcher',os.getenv('RAIL_DISPATCHER_PASSWORD','demo-dispatch'))
arrival=[];versions=[];last_id=''
with httpx.Client(auth=auth,timeout=15) as client:
    with client.stream('GET',base+'/api/stream') as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith('id: '):
                last_id=line[4:]
            if line.startswith('data: '):
                state=json.loads(line[6:])
                arrival.append(time.perf_counter());versions.append(state['version'])
                assert {'trains','signals','switches','incidents','plan_version'}<=state.keys()
                if len(arrival)==12:
                    break
    with client.stream('GET',base+'/api/stream',headers={'Last-Event-ID':last_id}) as response:
        for line in response.iter_lines():
            if line.startswith('data: '):
                restored=json.loads(line[6:])
                assert restored['version']>versions[-1]
                break
intervals=[(b-a)*1000 for a,b in zip(arrival,arrival[1:])]
# First event is immediate rather than aligned to publication, so omit its partial interval.
steady=intervals[1:]
result={'samples':len(arrival),'steady_intervals_ms':[round(v,3) for v in steady],'median_interval_ms':round(statistics.median(steady),3),'max_interval_ms':round(max(steady),3),'mean_hz':round(1000/statistics.mean(steady),3),'monotonic_versions':all(a<b for a,b in zip(versions,versions[1:])),'reconnect_full_state':True,'scope':'Read-only localhost SSE; live simulation may be paused. First partial interval omitted.'}
(Path(__file__).resolve().parents[1]/'artifacts/realtime-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
assert result['max_interval_ms']<1000
