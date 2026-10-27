@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See docs/MEMEFLOW.md
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "tools\memeflow.py"
exit /b 0
