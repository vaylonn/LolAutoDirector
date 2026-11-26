@echo off
rem Opens the LoL Auto Director app (from source).
cd /d "%~dp0.."
if not exist .venv\Scripts\pythonw.exe (echo Run windows\install.bat first & pause & exit /b 1)
start "" ".venv\Scripts\pythonw.exe" -m lolauto
