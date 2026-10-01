#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python -m uvicorn backend.app:app --host "${RAIL_HOST:-127.0.0.1}" --port "${RAIL_PORT:-8000}" --workers 1
