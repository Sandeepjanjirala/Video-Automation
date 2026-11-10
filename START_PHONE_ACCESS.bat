@echo off
setlocal
cd /d "%~dp0"
rem Same as START_WINDOWS.bat, but phones and other devices on this Wi-Fi can open the studio
rem after signing in with the password set in Settings ("Password for phone and other devices").
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_WINDOWS.bat first.
  pause
  exit /b 1
)
set STUDIO_LAN=1
".venv\Scripts\python.exe" run.py %*
pause
