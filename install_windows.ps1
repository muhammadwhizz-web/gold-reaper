<#
════════════════════════════════════════════════════════════════
 GOLD REAPER APEX :: Windows installer + boot autostart
 bot + dashboard (:8050) + weekly retrain, all via Task Scheduler
 usage:  powershell -ExecutionPolicy Bypass -File install_windows.ps1
════════════════════════════════════════════════════════════════
#>
$ErrorActionPreference = "Stop"
Write-Host "██ GOLD REAPER APEX windows installer" -ForegroundColor Red

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
Write-Host "[OK] APEX dependencies installed" -ForegroundColor Green

# 3. config
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[i] created .env - EDIT IT before going live" -ForegroundColor Yellow
}

# 4. data layer
Write-Host "[i] ingesting multi-timeframe history..." -ForegroundColor Yellow
python data\ingest_multi_tf.py
Write-Host "[i] forging feature matrix..." -ForegroundColor Yellow
python features\build_features.py
Write-Host "[i] pulling economic calendar..." -ForegroundColor Yellow
python data\news_ingest.py

# 5. Task Scheduler entries
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
             -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
             -StartWhenAvailable

$botAction = New-ScheduledTaskAction -Execute "$Dir\.venv\Scripts\python.exe" `
               -Argument "$Dir\bot.py" -WorkingDirectory $Dir
Register-ScheduledTask -TaskName "GOLD-REAPER" `
    -Action $botAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
    -Settings $Settings -Force | Out-Null

$dashAction = New-ScheduledTaskAction -Execute "$Dir\.venv\Scripts\python.exe" `
                -Argument "$Dir\core\dashboard.py 8050" -WorkingDirectory $Dir
Register-ScheduledTask -TaskName "GOLD-REAPER-DASHBOARD" `
    -Action $dashAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
    -Settings $Settings -Force | Out-Null

$retrainAction = New-ScheduledTaskAction -Execute "$Dir\.venv\Scripts\python.exe" `
                   -Argument "$Dir\ml\retrain_schedule.py" -WorkingDirectory $Dir
$retrainTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "21:30"
Register-ScheduledTask -TaskName "GOLD-REAPER-RETRAIN" `
    -Action $retrainAction -Trigger $retrainTrigger -Settings $Settings -Force | Out-Null

Write-Host "[OK] tasks registered: GOLD-REAPER / -DASHBOARD / -RETRAIN (boot-persistent)" -ForegroundColor Green

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host " APEX INSTALLED" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host "  bot       : Start-ScheduledTask -TaskName GOLD-REAPER"
Write-Host "  dashboard : http://localhost:8050 (auto at logon)"
Write-Host "  retrain   : weekly Sunday 21:30 (auto)"
Write-Host "  logs      : Get-Content data\reaper.log -Wait"
Write-Host "  audit     : Get-Content data\audit.jsonl -Tail 50 -Wait"
Write-Host "  breakers  : python bot.py --reset-breakers"
Write-Host ""
Write-Host "  RULE #1: run PAPER_MODE=true for at least 2 weeks." -ForegroundColor Red
Write-Host "  RULE #2: never risk money you cannot burn." -ForegroundColor Red
Write-Host "================================================" -ForegroundColor Green
