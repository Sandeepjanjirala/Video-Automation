@echo off
setlocal
cd /d "%~dp0"
echo Telugu Reel Studio - local setup
echo Your existing ShortsGenius folder is not modified.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if not errorlevel 1 goto install
  echo Existing .venv is pointing to a missing Python installation. Recreating...
  rmdir /s /q .venv
)
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv .venv
  goto checkvenv
)
py --version >nul 2>&1
if not errorlevel 1 (
  py -m venv .venv
  goto checkvenv
)
python --version >nul 2>&1
if errorlevel 1 (
  echo Install 64-bit Python 3.11 through 3.14 from python.org, then run this file again.
  pause
  exit /b 1
)
python -c "import sys; assert sys.version_info.major == 3 and sys.version_info.minor in range(11,15), 'Use Python 3.11 through 3.14; 3.12 is recommended'"
if errorlevel 1 (
  pause
  exit /b 1
)
python -m venv .venv
:checkvenv
if not exist ".venv\Scripts\python.exe" (
  echo Could not create the virtual environment.
  pause
  exit /b 1
)
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Read the error above and retry.
  pause
  exit /b 1
)
echo.
echo Setup complete. Run START_WINDOWS.bat, then open http://127.0.0.1:8787
echo Set FFmpeg and FFprobe paths in the app's Settings if they are not on PATH.
pause
