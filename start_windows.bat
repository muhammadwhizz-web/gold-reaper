@echo off
rem ============================================================
rem  GOLD REAPER :: windows launcher
rem  - activates the venv (share install or fresh clone)
rem  - first run: setup wizard
rem  - starts bot + dashboard in background windows
rem  - opens http://localhost:8080 in your browser
rem ============================================================
setlocal EnableExtensions
title Gold Reaper
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ---- locate install layout -------------------------------------------
set "ROOT=%~dp0"
set "APPDIR=%ROOT%app"
set "VENV="
if exist "%APPDIR%\bot.py" (
    if exist "%ROOT%venv\Scripts\python.exe" set "VENV=%ROOT%venv"
    if not defined VENV if exist "%ROOT%app\.venv\Scripts\python.exe" set "VENV=%ROOT%app\.venv"
) else (
    set "APPDIR=%ROOT%"
    if exist "%ROOT%.venv\Scripts\python.exe" set "VENV=%ROOT%.venv"
    if not defined VENV if exist "%ROOT%venv\Scripts\python.exe" set "VENV=%ROOT%venv"
)
if not defined VENV (
    echo [!] no venv found. Run install_windows.ps1 first.
    pause
    exit /b 1
)
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"

cd /d "%APPDIR%"
if not exist "logs" mkdir "logs"
if not exist "data" mkdir "data"

rem ---- first run: wizard ------------------------------------------------
if not exist ".env" (
    echo [i] first run - starting setup wizard...
    echo.
    "%PY%" -m setup.wizard
    if errorlevel 1 (
        echo [!] wizard failed - see docs\TROUBLESHOOTING.md
        pause
        exit /b 1
    )
)

rem ---- stop a previous console-spawned bot on this app dir --------------
if exist "data\bot.pid" (
    for /f %%p in ('type "data\bot.pid" 2^>nul') do (
        taskkill /PID %%p /T /F >nul 2>&1
    )
    del /q "data\bot.pid" >nul 2>&1
)

rem ---- bot ---------------------------------------------------------------
echo [i] starting the reaper...
if exist "%PYW%" (
    start "GoldReaper Bot" /MIN "%PYW%" bot.py
) else (
    start "GoldReaper Bot" /MIN cmd /c ""%PY%" bot.py >> logs\bot_console.log 2>&1"
)

rem ---- dashboard + browser -----------------------------------------------
echo [i] starting dashboard on http://localhost:8080 ...
start "GoldReaper Dashboard" /MIN "%PYW%" dashboard\app.py
timeout /t 3 /nobreak >nul
start "" http://localhost:8080

rem ---- optional tray ------------------------------------------------------
if exist "%PYW%" start "GoldReaper Tray" /MIN "%PYW%" system_tray.py

echo.
echo ============================================================
echo  GOLD REAPER ONLINE
echo ============================================================
echo   bot       : background window "GoldReaper Bot"
echo   dashboard : http://localhost:8080  (opened in browser)
echo   logs      : %APPDIR%\data\reaper.log
echo   audit     : %APPDIR%\data\audit.jsonl
echo   stop      : tray icon right-click - Stop Reaper
echo               or: schtasks /End /TN GOLD-REAPER  (Task Scheduler install)
echo   restart   : run this file again
echo.
echo   RULE #1: PAPER_MODE=true for the first 2 weeks. No exceptions.
echo ============================================================
timeout /t 10 /nobreak >nul
exit /b 0
