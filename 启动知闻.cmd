@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
"%~dp0.venv\Scripts\python.exe" -m newspaper_agent.desktop
if errorlevel 1 pause
