#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x .venv/bin/python ]]; then
    railflow_python=.venv/bin/python
elif [[ -f .venv/Scripts/python.exe ]]; then
    railflow_python=.venv/Scripts/python.exe
else
    echo 'Missing .venv. Follow the installation steps in docs/runbook.md first.' >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    echo 'Missing .env. Copy .env.example to .env and configure it first.' >&2
    exit 1
fi
if [[ ! -f frontend/dist/index.html ]]; then
    echo 'Frontend is not built. Run: pnpm --dir frontend build' >&2
    exit 1
fi

export PYTHONUTF8=1
echo 'Starting RailFlow. Press Ctrl+C to stop its services.'
exec "$railflow_python" -X utf8 scripts/run_local.py --env-file .env "$@"
