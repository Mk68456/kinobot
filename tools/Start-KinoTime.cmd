@echo off
cd /d "%~dp0"
py -3.11 desktop_launcher.py
if errorlevel 1 pause
