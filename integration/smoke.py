"""Check the live shared application without rewriting existing recorded results."""

import asyncio
import json
import os
import time

import httpx
from websockets.asyncio.client import connect


async def main():
    base = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:

        async def get(path):
            response = await client.get(path)
            response.raise_for_status()
            return response.json()

        async def post(path, body=None):
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        for path in (
            "/",
            "/dispatcher/",
            "/network/",
            "/map.html",
            "/integration/assets/map-bridge.js",
        ):
            (await client.get(path)).raise_for_status()
        await post("/api/simulation/reset")
        try:
            topology = await get("/api/topology")
            await post("/api/simulation/speed", {"multiplier": 60})
            async with connect(base.replace("http", "ws", 1) + "/ws", max_size=10_000_000) as ws:
                initial = json.loads(await ws.recv())["payload"]
                assert len(initial["trains"]) >= 5
                await post("/api/simulation/start")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if (
                        event["type"] == "state.updated"
                        and sum(t["status"] == "moving" for t in event["payload"]["trains"]) >= 2
                    ):
                        break
                else:
                    raise AssertionError("Trains did not start moving")
                paused = await post("/api/simulation/pause")
                await post(
                    "/api/incidents",
                    {
                        "kind": "closure",
                        "target_id": topology["sections"][0]["id"],
                        "duration_s": 3600,
                    },
                )
                deadline = time.monotonic() + 30
                plans = None
                while time.monotonic() < deadline:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    if event["type"] == "replan.failed":
                        raise AssertionError(event)
                    if event["type"] == "replan.completed":
                        plans = event["payload"]
                        break
                assert plans and plans["plans"]
                candidate = plans["plans"][0]
                applied = await post("/api/plans/" + candidate["id"] + "/apply")
                assert applied["epoch"] == initial["epoch"]
                assert applied["sim_time_s"] == paused["sim_time_s"]
                assert applied["active_plan_id"] == candidate["id"]
                assert not applied["awaiting_plan"] and not candidate["violations"]
                diagnostics = await get("/api/logic/diagnostics")
                assert diagnostics["plan"]["id"] == candidate["id"]
                assert len(await get("/api/logic/scenarios")) > 0
                assert "text/csv" in (await client.get("/api/report.csv")).headers["content-type"]
                print(
                    json.dumps(
                        {
                            "passed": True,
                            "trains": len(initial["trains"]),
                            "shared_epoch": True,
                            "websocket": True,
                            "closure_replanned_and_applied": True,
                            "plans": len(plans["plans"]),
                            "planning_s": plans["elapsed_s"],
                            "native_plan_matches_api": True,
                            "csv": True,
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            await post("/api/simulation/reset")


if __name__ == "__main__":
    asyncio.run(main())
