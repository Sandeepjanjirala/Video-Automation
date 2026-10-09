@echo off
setlocal
cd /d "%~dp0\.."

echo [Telugu Reel Studio] Launching Phase 3 Distributed Architecture...
echo.

REM 1. Verify PostgreSQL
call "scripts\launch_postgres.bat"
if errorlevel 1 exit /b 1

REM 2. Verify Redis in WSL
echo [Telugu Reel Studio] Ensuring Redis is running in WSL Ubuntu...
wsl -u root -d Ubuntu -- redis-server --daemonize yes --appendonly yes --maxmemory 512mb --maxmemory-policy noeviction >nul 2>&1

REM 3. Test Redis port 6379 from Windows
".venv\Scripts\python.exe" -c "import socket; s = socket.socket(); exit(s.connect_ex(('127.0.0.1', 6379)))"
if errorlevel 1 (
    echo [ERROR] Redis is not reachable on 127.0.0.1:6379.
    exit /b 1
)
echo [OK] Redis is reachable on 127.0.0.1:6379 (persistence=AOF, policy=noeviction).

REM 4. Start Celery workers and dispatcher via WSL Linux in background
echo [Telugu Reel Studio] Starting Linux Celery worker pools and dispatcher...
start "Studio Phase 3 Celery & Dispatcher" wsl -d Ubuntu -- bash -c "cd /mnt/c/Users/Sandeep.J/Documents/Studio && bash scripts/launch_phase3_wsl.sh"

REM 5. Start API server in distributed mode on Windows
echo [Telugu Reel Studio] Starting Studio API Server in distributed mode...
set STUDIO_DISTRIBUTED=1
".venv\Scripts\python.exe" run.py

exit /b 0
