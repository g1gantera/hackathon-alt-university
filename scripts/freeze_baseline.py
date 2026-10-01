"""Generate the reviewed, reproducible initial schedule (run from project root)."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.domain import ROOT, topology, trains
from backend.app.metrics import DEFAULT_SETTINGS
from backend.app.planning import build_plans

state={'topology':topology(),'fleet':trains(),'sim_time_s':0,'state_version':0,'running':False,
       'speed':30,'incidents':[],'committed':[],'settings':DEFAULT_SETTINGS}
result=build_plans(state)
if not result['plans']:
    raise RuntimeError('No independently validated baseline found')
plan=result['plans'][0]
plan['id']='baseline-v1'
plan['label']='Исходное расписание'
(ROOT/'scenarios/baseline-plan.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
print(f"Baseline saved: {len(plan['movements'])} movements, {result['elapsed_s']} seconds")
