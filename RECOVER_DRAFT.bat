@echo off
setlocal
cd /d "%~dp0"
if not exist "data\studio.sqlite3" (
  echo Copy RECOVER_DRAFT.bat and RECOVER_DRAFT.py into your Studio folder beside run.py.
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 RECOVER_DRAFT.py
) else (
  python -X utf8 RECOVER_DRAFT.py
)
pause
