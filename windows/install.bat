@echo off
rem One-time setup from source: creates .venv and installs the dependencies.
rem Not needed if you use LoLAutoDirector.exe from the Releases page.
cd /d "%~dp0.."
if not exist logs mkdir logs
set "PY=python"
if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
echo Using %PY%
(
  "%PY%" -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
) > logs\install.log 2>&1
".venv\Scripts\python.exe" -c "import customtkinter, cv2, obsws_python" >nul 2>&1
if errorlevel 1 (echo Install FAILED, see logs\install.log & pause & exit /b 1)
echo Install OK. Next: double-click windows\start.bat
pause
