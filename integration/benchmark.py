"""Exercise an isolated live simulation; reads role credentials from .env.

Keep /dispatcher/ visible to collect render acknowledgements. API latency alone
must never be reported as proof of the UI's 500 ms requirement.
"""

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from websockets.asyncio.client import connect

load_dotenv()
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


async def main():
    rows = []
    async with httpx.AsyncClient(base_url=BASE, timeout=40) as client:
        if (await client.get("/api/auth/me")).status_code == 401:
            response = await client.post(
                "/api/auth/login",
                json={"role": "dispatcher", "password": os.environ.get("DISPATCHER_PASSWORD", "")},
            )
            response.raise_for_status()
        initial = (await client.get("/api/state")).json()
        auth = (await client.get("/api/auth/me")).json()
        report = {
            "engine": initial["engine"],
            "trains": len(initial["trains"]),
            "switch_count": len(initial["switches"]),
            "live_category": initial["metrics"].get("category"),
            "auth": auth,
            "runs": rows,
        }
        async with connect(
            BASE.replace("http", "ws") + "/ws",
            max_size=10_000_000,
            additional_headers={"Cookie": "; ".join(f"{k}={v}" for k, v in client.cookies.items())},
        ) as ws:
            events = []
            completions = asyncio.Queue()

            async def receive():
                async for message in ws:
                    stamp = time.perf_counter()
                    item = json.loads(message)
                    events.append((stamp, item["type"], item.get("seq")))
                    if item["type"] in ("replan.completed", "replan.failed"):
                        completions.put_nowait((stamp, item))

            receiver = asyncio.create_task(receive())
            await asyncio.sleep(8)
            idle = [t for t, k, _ in events if k == "state.updated"][1:]
            gaps = [b - a for a, b in zip(idle, idle[1:])]
            report["idle_state_stream"] = {
                "intervals": len(gaps),
                "mean_interval_s": round(statistics.mean(gaps), 4),
                "max_interval_s": round(max(gaps), 4),
                "hz": round(1 / statistics.mean(gaps), 4),
            }
            for mode in ["closure", "five_incidents", "batch_ten", "ten_incidents"]:
                (await client.post("/api/simulation/reset")).raise_for_status()
                while not completions.empty():
                    completions.get_nowait()
                samples = []
                stop = False

                async def ping():
                    while not stop:
                        started = time.perf_counter()
                        response = await client.get("/api/state")
                        samples.append((time.perf_counter() - started, response.status_code))
                        await asyncio.sleep(0.15)

                monitor = asyncio.create_task(ping())
                began = time.perf_counter()
                if mode == "closure":
                    target = (await client.get("/api/topology")).json()["sections"][0]["id"]
                    response = await client.post(
                        "/api/incidents",
                        json={"kind": "closure", "target_id": target, "duration_s": 600},
                    )
                elif mode in ("five_incidents", "batch_ten"):
                    sections = (await client.get("/api/topology")).json()["sections"]
                    response = await client.post(
                        "/api/incidents/batch",
                        json={
                            "incidents": [
                                {"kind": "signal", "target_id": s["id"], "duration_s": 7200}
                                for s in sections[: 5 if mode == "five_incidents" else 10]
                            ]
                        },
                    )
                else:
                    response = await client.post("/api/logic/scenarios/ten_incidents")
                response.raise_for_status()
                completed, item = await asyncio.wait_for(completions.get(), 30)
                stop = True
                await monitor
                starts = [t for t, k, _ in events if k == "replan.started" and t >= began]
                ticks = [
                    t for t, k, _ in events if k == "state.updated" and began <= t <= completed
                ]
                tick_gaps = [b - a for a, b in zip(ticks, ticks[1:])]
                data = item["payload"]
                plans = data.get("plans", [])
                diag = (await client.get("/api/logic/diagnostics")).json()
                if plans:
                    applied = await client.post("/api/plans/" + plans[0]["id"] + "/apply")
                    applied.raise_for_status()
                    valid = applied.json()["plan"]["applicable"]
                else:
                    valid = False
                rows.append(
                    {
                        "case": mode,
                        "constraints": len(diag["scenario"]["blocks"]),
                        "plans": len(plans),
                        "declared_calculation_s": data.get("elapsed_s"),
                        "within_budget": data.get("within_budget"),
                        "replan_started_to_received_s": round(completed - starts[0], 4)
                        if starts
                        else None,
                        "request_to_received_s": round(completed - began, 4),
                        "api_state_max_ms": round(max(t for t, c in samples) * 1000, 2),
                        "api_state_median_ms": round(
                            statistics.median(t for t, c in samples) * 1000, 2
                        ),
                        "api_samples": len(samples),
                        "api_errors": sum(c != 200 for t, c in samples),
                        "max_state_stream_gap_s": round(max(tick_gaps), 4) if tick_gaps else None,
                        "applied_valid_plan": valid,
                    }
                )
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        (await client.post("/api/simulation/reset")).raise_for_status()
        performance = (await client.get("/api/logic/performance")).json()
    report["ingestion"] = performance["ingestion"]
    report["ui_event_to_paint"] = {
        "verified": bool(performance["paint_samples"]),
        "under_500_ms": performance["paint_max_ms"] < 500 if performance["paint_samples"] else None,
        "samples": performance["paint_samples"],
        "max_ms": performance["paint_max_ms"],
        "note": "Requires a visible browser tab; HTTP timings do not prove UI paint latency.",
    }
    Path(os.environ.get("BENCHMARK_OUTPUT", "output/regulation-benchmark.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
