<#
════════════════════════════════════════════════════════════════
 GOLD REAPER :: Windows one-command installer
════════════════════════════════════════════════════════════════
 What it does (fresh machine -> hunting):
   1. checks Python 3.10+ (offers silent install from python.org / winget)
   2. copies the app to  %LOCALAPPDATA%\GoldReaper\app
   3. creates venv at    %LOCALAPPDATA%\GoldReaper\venv
   4. installs all requirements (MetaTrader5 included, for Exness)
   5. copies start_windows.bat + icon to the GoldReaper root
   6. runs the first-time setup wizard (broker, keys, risk)
   7. creates Desktop + Start Menu shortcuts: "Gold Reaper"
   8. registers Task Scheduler tasks (logon + boot, auto-restart):
        GOLD-REAPER            bot            (pythonw, hidden)
        GOLD-REAPER-DASHBOARD  dashboard :8080
        GOLD-REAPER-WATCHDOG   watchdog (restarts the bot if wedged)
        GOLD-REAPER-RETRAIN    weekly meta-model retrain
   9. prints success + exact log paths

 Usage (one command):
   powershell -ExecutionPolicy Bypass -File install_windows.ps1

 Called by GoldReaper-Setup.exe (Inno Setup):
   powershell ... install_windows.ps1 -FromInstaller -PayloadDir "C:\Program Files\GoldReaper"
════════════════════════════════════════════════════════════════
#>
param(
    [switch]$FromInstaller,
    [string]$PayloadDir = ""
)
$ErrorActionPreference = "Stop"

$Red    = "Red"; $Green = "Green"; $Yel = "Yellow"; $Gray = "DarkGray"
Write-Host "██ GOLD REAPER :: windows installer" -ForegroundColor $Red

# ---- paths --------------------------------------------------------------
$Target   = Join-Path $env:LOCALAPPDATA "GoldReaper"
$AppDir   = Join-Path $Target "app"
$VenvDir  = Join-Path $Target "venv"
$LogsDir  = Join-Path $Target "logs"
if (-not $PayloadDir) { $PayloadDir = $PSScriptRoot }
if (-not (Test-Path (Join-Path $PayloadDir "bot.py"))) {
    Write-Host "[!] payload error: bot.py not found under $PayloadDir" -ForegroundColor $Red
    exit 1
}

function Step($msg)   { Write-Host "[i] $msg" -ForegroundColor $Yel }
function Ok($msg)     { Write-Host "[OK] $msg" -ForegroundColor $Green }
function Fail($msg)   { Write-Host "[!] $msg" -ForegroundColor $Red; Read-Host "press Enter to exit"; exit 1 }

# ---- 1. python 3.10+ ----------------------------------------------------
function Get-PyVersion {
    try {
        $v = (& python --version) 2>$null
        if ($v -match "Python (\d+)\.(\d+)") { return [version]("{0}.{1}" -f $Matches[1], $Matches[2]) }
    } catch {}
    return $null
}
$pyVer = Get-PyVersion
if (-not $pyVer -or $pyVer -lt [version]"3.10" -or $pyVer -ge [version]"3.13") {
    Step "Python 3.10-3.12 not found (found: $pyVer). Installing Python 3.12 silently..."
    $installed = $false
    try {
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        $installed = $true
    } catch {
        Step "winget unavailable - falling back to python.org installer"
        try {
            $exe = Join-Path $env:TEMP "python-3.12.8-amd64.exe"
            Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile $exe -UseBasicParsing
            Start-Process $exe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
            $installed = $true
        } catch {
            Fail "could not install Python automatically. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run this installer."
        }
    }
    if ($installed) {
        # refresh PATH for this session
        $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
        $pyVer = Get-PyVersion
        if (-not $pyVer -or $pyVer -lt [version]"3.10") {
            Fail "Python installed but not on PATH yet. Close this window, reopen, and run the installer again."
        }
        Ok "Python $pyVer ready"
    }
} else {
    Ok "Python $pyVer found"
}

# ---- 2. copy app payload ------------------------------------------------
Step "copying app -> $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir, $VenvDir, $LogsDir | Out-Null
robocopy $PayloadDir $AppDir /E /NFL /NDL /NJH /NJS /NP `
  /XD ".git" ".venv" "venv" "__pycache__" ".pytest_cache" ".mypy_cache" "node_modules" `
  /XF ".env" "*.pyc" "reaper.log" "bot_console.log" | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy failed (code $LASTEXITCODE)" }
# never clobber an existing runtime state / model artifacts
Ok "app copied (data/, .env, venv preserved)"

# ---- 3. venv + requirements ---------------------------------------------
Step "creating venv at $VenvDir"
& python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { Fail "venv creation failed - see docs\TROUBLESHOOTING.md #3" }
$PyExe  = Join-Path $VenvDir "Scripts\python.exe"
$PywExe = Join-Path $VenvDir "Scripts\pythonw.exe"
& $PyExe -m pip install --upgrade pip -q
Step "installing requirements (this can take a few minutes)..."
& $PyExe -m pip install -r (Join-Path $AppDir "requirements.txt") -q
if ($LASTEXITCODE -ne 0) { Fail "pip install failed - check internet/proxy (docs\TROUBLESHOOTING.md #3)" }
Ok "APEX stack installed (incl. MetaTrader5 for Exness)"

# ---- 4. launcher + icon ---------------------------------------------------
Copy-Item (Join-Path $PayloadDir "start_windows.bat") $Target -Force
Copy-Item (Join-Path $PayloadDir "assets\icon.ico")    $Target -Force
Copy-Item (Join-Path $PayloadDir "assets\icon.png")    $Target -Force
# data + logs live inside app dir; keep a logs/ mirror path documented
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
Ok "launcher + icon in place"

# ---- 5. shortcuts (Desktop + Start Menu) ----------------------------------
$wsh = New-Object -ComObject WScript.Shell
foreach ($lnk in @(
    @{ Dir = [Environment]::GetFolderPath("Desktop") },
    @{ Dir = Join-Path [Environment]::GetFolderPath("Programs") "Gold Reaper" }
)) {
    New-Item -ItemType Directory -Force -Path $lnk.Dir | Out-Null
    $s = $wsh.CreateShortcut((Join-Path $lnk.Dir "Gold Reaper.lnk"))
    $s.TargetPath    = "$env:COMSPEC"
    $s.Arguments     = "/c `"$Target\start_windows.bat`""
    $s.WorkingDirectory = $Target
    $s.IconLocation  = Join-Path $Target "icon.ico"
    $s.Description   = "Autonomous XAU/USD hunter"
    $s.WindowStyle   = 7   # minimized
    $s.Save()
}
Ok "desktop + start menu shortcut: Gold Reaper"

# ---- 6. setup wizard (skipped if .env already exists) ----------------------
if (-not (Test-Path (Join-Path $AppDir ".env"))) {
    Step "first-time setup wizard"
    Push-Location $AppDir
    & $PyExe -m setup.wizard
    $wiz = $LASTEXITCODE
    Pop-Location
    if ($wiz -ne 0) {
        Write-Host "[i] wizard skipped/failed - it will run at first launch (start_windows.bat)" -ForegroundColor $Yel
    } else {
        Ok ".env written + broker tested"
    }
} else {
    Ok ".env already exists (wizard preserved your config)"
}

# ---- 7. Task Scheduler (logon + boot, restart 60s, 10y, hidden) ------------
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
             -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
             -StartWhenAvailable -Hidden
$Triggers = @( (New-ScheduledTaskTrigger -AtLogOn), (New-ScheduledTaskTrigger -AtStartup) )

function Reg-Task($name, $args_) {
    $action = New-ScheduledTaskAction -Execute $PywExe -Argument $args_ -WorkingDirectory $AppDir
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $Triggers `
        -Settings $Settings -Force | Out-Null
}
try {
    Reg-Task "GOLD-REAPER"           "bot.py"
    Reg-Task "GOLD-REAPER-DASHBOARD" "dashboard\app.py"
    Reg-Task "GOLD-REAPER-WATCHDOG"  "watchdog.py"
    $retrainAction = New-ScheduledTaskAction -Execute $PywExe -Argument "ml\retrain_schedule.py" -WorkingDirectory $AppDir
    $retrainTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "21:30"
    Register-ScheduledTask -TaskName "GOLD-REAPER-RETRAIN" -Action $retrainAction `
        -Trigger $retrainTrigger -Settings $Settings -Force | Out-Null
    Ok "tasks registered: GOLD-REAPER / -DASHBOARD / -WATCHDOG / -RETRAIN"
    Start-ScheduledTask -TaskName "GOLD-REAPER-DASHBOARD" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "GOLD-REAPER"           -ErrorAction SilentlyContinue
} catch {
    Write-Host "[!] task registration failed: $_ (bot still runs via start_windows.bat)" -ForegroundColor $Yel
}

# ---- 8. success -------------------------------------------------------------
Write-Host ""
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
Write-Host " GOLD REAPER INSTALLED" -ForegroundColor $Green
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
Write-Host "  app        : $AppDir"
Write-Host "  venv       : $VenvDir"
Write-Host "  dashboard  : http://localhost:8080  (auto at logon)"
Write-Host "  bot log    : $AppDir\data\reaper.log"
Write-Host "  console log: $LogsDir  +  $AppDir\logs\bot_console.log"
Write-Host "  audit trail: $AppDir\data\audit.jsonl"
Write-Host "  stop bot   : Stop-ScheduledTask -TaskName GOLD-REAPER"
Write-Host "  breakers   : $PyExe bot.py --reset-breakers"
Write-Host "  uninstall  : Settings -> Apps -> Gold Reaper"
Write-Host ""
Write-Host "  RULE #1: PAPER_MODE=true for at least 2 weeks." -ForegroundColor $Red
Write-Host "  RULE #2: never risk money you cannot burn."    -ForegroundColor $Red
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
if (-not $FromInstaller) { Read-Host "press Enter to close" }
exit 0
