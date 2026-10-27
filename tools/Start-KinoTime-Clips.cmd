@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See docs/LOCAL_CLIPS.md
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "tools\clips_studio.py"
if errorlevel 1 pause
