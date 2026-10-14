@echo off
setlocal
cd /d "%~dp0\.."
echo [Telugu Reel Studio] Checking PostgreSQL database connectivity...

".venv\Scripts\python.exe" -c "import psycopg2, os; psycopg2.connect(os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio').replace('+psycopg2', ''), connect_timeout=2)" >nul 2>&1
if not errorlevel 1 (
    echo [OK] PostgreSQL is already running and reachable at 127.0.0.1:5433
    goto done
)

echo [INFO] Attempting to start PostgreSQL via WSL Ubuntu service...
wsl -u root -d Ubuntu -- service postgresql start >nul 2>&1

".venv\Scripts\python.exe" -c "import psycopg2, os; psycopg2.connect(os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio').replace('+psycopg2', ''), connect_timeout=2)" >nul 2>&1
if not errorlevel 1 (
    echo [OK] PostgreSQL started successfully in WSL Ubuntu.
    goto done
)

echo [INFO] Attempting to start PostgreSQL via Docker Compose...
docker compose up -d postgres >nul 2>&1

".venv\Scripts\python.exe" -c "import psycopg2, os; psycopg2.connect(os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio').replace('+psycopg2', ''), connect_timeout=2)" >nul 2>&1
if not errorlevel 1 (
    echo [OK] PostgreSQL started successfully via Docker Compose.
    goto done
)

echo.
echo [ERROR] Could not establish connection to PostgreSQL on port 5433.
echo Actionable remedies:
echo 1. Start PostgreSQL in WSL: wsl -u root -d Ubuntu -- service postgresql start
echo 2. Start Docker Desktop and run: docker compose up -d postgres
echo 3. Check STUDIO_DATABASE_URL environment variable or settings.json.
echo.
exit /b 1

:done
echo [OK] Database service ready.
exit /b 0
