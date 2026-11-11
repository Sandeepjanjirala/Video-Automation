@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_WINDOWS.bat first.
  pause
  exit /b 1
)
rem Prefer the project-local test runtime without changing the system Node installation.
if exist ".local-tools\node_modules\node\bin\node.exe" set "PATH=%~dp0.local-tools\node_modules\node\bin;%PATH%"
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" manage.py check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\run_audit_tests.py
if errorlevel 1 goto failed
echo All local verification checks passed.
pause
exit /b 0
:failed
echo Verification failed. See the error above.
pause
exit /b 1
