<#
════════════════════════════════════════════════════════════════
 GOLD REAPER :: Windows installer + boot autostart (Task Scheduler)
 usage:  right-click -> Run with PowerShell
         (or: powershell -ExecutionPolicy Bypass -File install_windows.ps1)
════════════════════════════════════════════════════════════════
#>
$ErrorActionPreference = "Stop"
Write-Host "██ GOLD REAPER windows installer" -ForegroundColor Red

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir

# 1. python check
try { $py = (python --version) 2>$null } catch { $py = $null }
if (-not $py) {
    Write-Host "[i] python not found - install from python.org (tick 'Add to PATH')" -ForegroundColor Yellow
    Start-Process "https://www.python.org/downloads/"
    exit 1
}

# 2. venv + deps
python -m venv .venv
& ".\.venv\Scripts\Activate.ps1"
pip install --upgrade pip -q
pip install -r requirements.txt -q
Write-Host "[OK] dependencies installed (incl. MetaTrader5 bridge)" -ForegroundColor Green

# 3. config
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[i] created .env - EDIT IT before going live" -ForegroundColor Yellow
}

# 4. history + backtest
Write-Host "[i] fetching historical data..." -ForegroundColor Yellow
python data\fetch_history.py

# 5. Task Scheduler entry (autostart at logon, restart on failure)
$Action  = New-ScheduledTaskAction -Execute "$Dir\.venv\Scripts\python.exe" `
                                     -Argument "$Dir\bot.py" -WorkingDirectory $Dir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
             -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
             -StartWhenAvailable
Register-ScheduledTask -TaskName "GOLD-REAPER" -Action $Action `
    -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "[OK] Task Scheduler entry 'GOLD-REAPER' registered (starts at logon)" -ForegroundColor Green

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host " REAPER INSTALLED" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host "  start now : Start-ScheduledTask -TaskName GOLD-REAPER"
Write-Host "  stop      : Stop-ScheduledTask  -TaskName GOLD-REAPER"
Write-Host "  remove    : Unregister-ScheduledTask -TaskName GOLD-REAPER"
Write-Host "  logs      : Get-Content data\reaper.log -Wait"
Write-Host "  config    : edit .env then restart the task"
Write-Host ""
Write-Host "  RULE #1: run PAPER_MODE=true for at least 2 weeks." -ForegroundColor Red
Write-Host "  RULE #2: never risk money you cannot burn." -ForegroundColor Red
Write-Host "================================================" -ForegroundColor Green
