@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || (echo Python not found. Install it from python.org and tick "Add to PATH". & pause & exit /b 1)
)
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1 || (
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt && copy /y requirements.txt ".venv\requirements.installed" >nul
)
start "" ".venv\Scripts\pythonw.exe" app.py --background
