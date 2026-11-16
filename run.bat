@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up...
  python -m venv .venv || (echo Python not found. Install it from python.org and tick "Add to PATH". & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
)
rem (Re)install extras when requirements.txt is new or changed (e.g. after an update)
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1 || (
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt && copy /y requirements.txt ".venv\requirements.installed" >nul
)
start "" ".venv\Scripts\pythonw.exe" app.py
