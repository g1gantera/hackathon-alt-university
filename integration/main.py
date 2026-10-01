"""One origin and one native logic simulation shared by both interfaces.

Run from the repository root: python -m uvicorn integration.main:app
The shared backend retains its API and WebSocket paths.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.app import main as backend

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "integration/static"
MAP_ADAPTER = (
    '\n<link rel="stylesheet" href="/integration/assets/map-bridge.css">\n'
    '<script type="module" src="/integration/assets/map-bridge.js"></script>\n'
)


@asynccontextmanager
async def lifespan(app):
    # Mounted applications do not run their own lifespan automatically.
    async with backend.app.router.lifespan_context(backend.app):
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(STATIC / "index.html")


@app.get("/dispatcher/", include_in_schema=False)
async def dispatcher():
    index = ROOT / "frontend/dist/index.html"
    if not index.is_file():
        raise HTTPException(503, "Build frontend first; see INTEGRATION.md")
    return FileResponse(index)


@app.get("/network/", include_in_schema=False)
async def network_view():
    # Only the served response gets an additional script; map.html stays byte-identical.
    source = (ROOT / "map.html").read_bytes().decode("utf-8")
    return HTMLResponse(source.replace("</body>", MAP_ADAPTER + "</body>", 1))


@app.get("/map.html", include_in_schema=False)
async def original_map():
    return FileResponse(ROOT / "map.html")


@app.get("/map_data.js", include_in_schema=False)
@app.get("/network/map_data.js", include_in_schema=False)
async def map_data():
    return FileResponse(ROOT / "map_data.js", media_type="application/javascript")


@app.get("/network.json", include_in_schema=False)
@app.get("/network/network.json", include_in_schema=False)
async def original_graph():
    return FileResponse(ROOT / "network.json", media_type="application/json")


@app.get("/api/integration")
async def integration_status(request: Request):
    backend.require(request, "viewer")
    return {
        "engine": backend.sim.state.get("engine", "demo"),
        "dispatcher": "/dispatcher/",
        "map": "/network/",
        "original_map": "/map.html",
        "network_graph": "/network.json",
        "state": "/api/state",
        "websocket": "/ws",
        "frontend_built": (ROOT / "frontend/dist/index.html").is_file(),
        "simulation_count": 1,
        "simulation_count_scope": "primary logic state; optional execution trial is isolated",
        "execution_trial": "/api/execution/state",
    }


app.mount("/integration/assets", StaticFiles(directory=STATIC), name="integration-assets")
# All existing auth, API, planning, assets and /ws routes retain their original URLs.
app.mount("/", backend.app, name="original-dispatcher")
