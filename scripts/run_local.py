"""Run ingestion and dispatcher as separate local processes; Ctrl+C stops both."""

import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env")
if os.environ.get("DEMO_MODE", "false") != "true" and not all(
    os.environ.get(k + "_PASSWORD") for k in ("ADMIN", "DISPATCHER", "VIEWER")
):
    raise SystemExit("Run .venv/bin/python scripts/setup_local.py first, or fill .env.")
if not os.environ.get("INGEST_TOKEN"):
    raise SystemExit("INGEST_TOKEN is required in .env.")
os.environ.setdefault("INGEST_URL", "http://127.0.0.1:8001")
children = []
try:
    for module, port in [
        ("backend.ingestion.service:app", "8001"),
        ("integration.main:app", "8000"),
    ]:
        children.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", port],
                cwd=root,
            )
        )
    print("Dispatcher: http://127.0.0.1:8000/dispatcher/ (credentials in .env)", flush=True)
    while all(p.poll() is None for p in children):
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    for p in children:
        if p.poll() is None:
            p.terminate()
    for p in children:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
