@echo off
rem Builds LoLAutoDirector.exe (one file: app + director + agent + tools) at the project root.
rem Takes 2-3 minutes. Log: logs\build.log
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (echo Run windows\install.bat first & pause & exit /b 1)
if not exist logs mkdir logs
set "VPY=.venv\Scripts\python.exe"
rem Anaconda keeps its DLLs (Tcl/Tk, OpenSSL...) in <anaconda>\Library\bin: add it so PyInstaller bundles them.
"%VPY%" -c "import sys; open('logs/base_python.txt', 'w').write(sys.base_prefix)"
set /p BASEPY=<logs\base_python.txt
if exist "%BASEPY%\Library\bin" set "PATH=%BASEPY%\Library\bin;%PATH%"
echo Base Python: %BASEPY% > logs\build.log
"%VPY%" -m pip install -r requirements.txt >> logs\build.log 2>&1
"%VPY%" -m pip install --upgrade pyinstaller >> logs\build.log 2>&1
"%VPY%" -c "import customtkinter, cv2, obsws_python, numpy" >> logs\build.log 2>&1
if errorlevel 1 (echo FAILED: missing dependencies, see logs\build.log >> logs\build.log & echo FAILED: missing dependencies, see logs\build.log & pause & exit /b 1)
rem Build in a fresh folder so a running old exe does not block the build.
if exist build\exe rmdir /s /q build\exe
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name LoLAutoDirector --collect-all cv2 --collect-all customtkinter ^
    --distpath build\exe --workpath build\work --specpath build run.py >> logs\build.log 2>&1
if errorlevel 1 goto echec
if not exist build\exe\LoLAutoDirector.exe goto echec
rem A running exe cannot be replaced but can be renamed: move it aside.
del /f /q LoLAutoDirector_old_*.exe >nul 2>&1
if exist LoLAutoDirector.exe del /f /q LoLAutoDirector.exe >nul 2>&1
if exist LoLAutoDirector.exe ren LoLAutoDirector.exe LoLAutoDirector_old_%RANDOM%.exe
copy /y build\exe\LoLAutoDirector.exe LoLAutoDirector.exe >nul || goto verrou
echo OK: LoLAutoDirector.exe built. >> logs\build.log
echo OK: LoLAutoDirector.exe built in the project folder.
goto fin
:verrou
echo Copy FAILED: close LoLAutoDirector.exe and retry (the exe is in build\exe\). >> logs\build.log
echo Copy FAILED: close LoLAutoDirector.exe and retry.
goto fin
:echec
echo FAILED: see logs\build.log >> logs\build.log
echo FAILED: see logs\build.log
:fin
echo FIN >> logs\build.log
pause
