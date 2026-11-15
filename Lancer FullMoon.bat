@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Installez les dependances selon README.md avant de lancer.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B "app\app.py"
pause
