@echo off
REM ═══════════════════════════════════════════════════
REM  GOLD REAPER :: quick launcher (double-click me)
REM ═══════════════════════════════════════════════════
cd /d "%~dp0"
if not exist ".venv" (
  echo [!] run install_windows.ps1 first
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python bot.py
pause
