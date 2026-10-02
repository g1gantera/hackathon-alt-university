@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Follow the installation steps in docs\runbook.md first.
    popd
    exit /b 1
)
if not exist ".env" (
    echo Missing .env. Copy .env.example to .env and configure it first.
    popd
    exit /b 1
)
if not exist "frontend\dist\index.html" (
    echo Frontend is not built. Run: pnpm --dir frontend build
    popd
    exit /b 1
)

set "PYTHONUTF8=1"
echo Starting RailFlow. Press Ctrl+C to stop its services.
".venv\Scripts\python.exe" -X utf8 "scripts\run_local.py" --env-file ".env" %*
set "railflow_exit=%errorlevel%"
popd
exit /b %railflow_exit%
