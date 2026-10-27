@echo off
cd /d "%~dp0"
python tools\zona_prototype.py
if errorlevel 1 pause
