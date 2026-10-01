"""Randomised single-corridor traffic: automatic dispatch versus whole-leg locking.

Each trial picks a real 30–90 km corridor from the supplied graph and runs
3–6 trains in both directions with random departures, priorities, speeds and
lengths. The same trial runs twice: with ``auto_dispatch`` (passing-point
sections, receiving tracks, overtakes) and without it (every train commits
its whole leg to the next stop). Unfinished trains count as late until the
end of the trial, so stalls are not hidden by missing arrivals.

    .venv/bin/python scripts/dispatch_stress.py --trials 24 --write
"""
import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.engine import Engine
from backend.history import History
from backend.meets import weight
from backend.models import Settings, TrainSpec
from backend.network import Network

ROOT = Path(__file__).resolve().parents[1]
_network = None


class JournalOnly(History):
    """Keep the event journal but not per-second replay snapshots (memory)."""

    def snapshot(self, state, retention_hours=48):
        pass


def network():
    global _network
    if _network is None:
        _network = Network()
    return _network


def scenario(seed):
    """A reproducible corridor and train set for one trial."""
    net = network()
    rng = random.Random(seed)
    stations = [s for s in net.stations if s['vertex'] is not None and s['type'] != 'tram_stop' and s['name']]
    while True:
        a, b = rng.sample(stations, 2)
        route = net.route(a['vertex'], b['vertex'], limit_m=90000)
        if (route and 30000 < net.lengths(route)[-1] < 90000 and len({e for e, _ in route}) == len(route)
                and net.route(b['vertex'], a['vertex'], limit_m=90000)):
            break
    length = net.lengths(route)[-1]
    trains = []
    for i in range(rng.randint(3, 6)):
        forward = rng.random() < .5
        speed = rng.choice([60, 80, 100, 120])
        departure = rng.randint(0, 900)
        trains.append(dict(id=f'T{i}', name=f'T{i}', origin=a['vertex'] if forward else b['vertex'],
            destination=b['vertex'] if forward else a['vertex'], departure_s=departure,
            importance=rng.randint(1, 10), max_speed_kmh=speed, length_m=rng.choice([180, 300, 500]),
            scheduled_arrival_s=round(departure+length/(min(speed, 80)/3.6)*1.15+60)))
    return {'seed': seed, 'corridor_km': round(length/1000, 1), 'trains': trains}


def run(job):
    seed, auto, horizon = job
    case = scenario(seed)
    engine = Engine(network(), JournalOnly(':memory:'), Settings(auto_dispatch=auto))
    for spec in case['trains']:
        try:
            engine.add_train(TrainSpec(**spec), replan=False)
        except ValueError:
            pass
    engine.replan('stress trial')
    started = time.perf_counter()
    errors = deadlock = 0
    while engine.sim_time < horizon and any(t.state != 'completed' for t in engine.trains.values()):
        engine.advance(5)
        errors += len(engine.invariant_errors())
        deadlock += bool(engine.deadlock)
    late = weighted = 0.0
    for t in engine.trains.values():
        target = t.spec.scheduled_arrival_s
        done = t.state in {'arrived', 'completed'} and t.arrivals
        arrival = t.arrivals[-1]['time'] if done else engine.sim_time
        lateness = max(0, arrival-target)
        late += lateness
        weighted += weight(engine, t)*lateness
    return {'seed': seed, 'auto_dispatch': auto, 'corridor_km': case['corridor_km'], 'trains': len(engine.trains),
        'completed': sum(t.state == 'completed' for t in engine.trains.values()), 'safety_errors': errors,
        'deadlock_samples': deadlock, 'late_s': round(late), 'weighted_late_s': round(weighted),
        'end_s': round(engine.sim_time), 'decisions': len(engine.decisions), 'wall_s': round(time.perf_counter()-started, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--trials', type=int, default=16)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--horizon', type=float, default=13000)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--write', action='store_true', help='write artifacts/dispatch-stress.json')
    args = parser.parse_args()
    seeds = [args.seed*1000+i for i in range(args.trials)]
    jobs = [(s, auto, args.horizon) for s in seeds for auto in (False, True)]
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(run, jobs))
    by = {(r['seed'], r['auto_dispatch']): r for r in rows}
    print(f'{"seed":>6} {"km":>5} {"n":>2} | {"whole-leg done/late/weighted":>30} | {"automatic done/late/weighted":>30} | dl')
    for s in seeds:
        off, on = by[(s, False)], by[(s, True)]
        print(f'{s:>6} {on["corridor_km"]:>5} {on["trains"]:>2} | {off["completed"]:>4}/{off["late_s"]:>8}/{off["weighted_late_s"]:>9}        |'
              f' {on["completed"]:>4}/{on["late_s"]:>8}/{on["weighted_late_s"]:>9}        | {on["deadlock_samples"]}')
    summary = {}
    for auto in (False, True):
        group = [r for r in rows if r['auto_dispatch'] == auto]
        summary['automatic' if auto else 'whole_leg'] = {
            'completed': sum(r['completed'] for r in group), 'trains': sum(r['trains'] for r in group),
            'safety_errors': sum(r['safety_errors'] for r in group),
            'trials_with_deadlock': sum(r['deadlock_samples'] > 0 for r in group),
            'late_s': sum(r['late_s'] for r in group), 'weighted_late_s': sum(r['weighted_late_s'] for r in group),
            'median_weighted_late_s': statistics.median(r['weighted_late_s'] for r in group)}
    better = sum(by[(s, True)]['weighted_late_s'] < by[(s, False)]['weighted_late_s'] for s in seeds)
    worse = sum(by[(s, True)]['weighted_late_s'] > by[(s, False)]['weighted_late_s'] for s in seeds)
    summary['trials'] = len(seeds)
    summary['automatic_better_trials'], summary['automatic_worse_trials'] = better, worse
    print(json.dumps(summary, indent=2))
    if args.write:
        out = ROOT/'artifacts/dispatch-stress.json'
        out.write_text(json.dumps({'note': 'Local simulation of random traffic on real mapped corridors; not a field benchmark.',
            'horizon_s': args.horizon, 'summary': summary, 'trials': rows}, indent=2)+'\n')
        print('wrote', out)


if __name__ == '__main__':
    main()
