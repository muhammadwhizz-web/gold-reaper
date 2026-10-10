# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: Windows one-command installer (P1-E repair build)
#═════════════════════════════════════════════════════════════════
#  Contract: py -3.12 -> winget Python.Python.3.12 (EXPLICIT exit-code
#  check, no try/catch guesswork) -> validated python -> venv at project
#  root -> pinned requirements.lock -> Task Scheduler (auto-restart) ->
#  desktop shortcut -> installer/health_check.py GATE: SUCCESS only on
#  pass. Idempotent (stops old tasks first). Never overwrites .env.
#    .\install_windows.ps1 -Uninstall [-Purge]
# ═══════════════════════════════════════════════════════════════
param(
    [switch]$Uninstall,
    [switch]$Purge
)
$ErrorActionPreference = "Stop"
$Red = "Red"; $Green = "Green"; $Yel = "Yellow"; $Gray = "DarkGray"
Write-Host "██ gold-reaper :: windows installer (reliability build)" -ForegroundColor $Red

$Src    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Share  = Join-Path $env:LOCALAPPDATA "GoldReaper"
$AppDir = Join-Path $Share "app"
$Venv   = Join-Path $AppDir ".venv"
$LogsDir = Join-Path $Share "logs"

function Ok([string]$m)   { Write-Host "[OK] $m" -ForegroundColor $Green }
function Step([string]$m) { Write-Host "[i] $m"  -ForegroundColor $Yel }
function Warn([string]$m) { Write-Host "[!] $m"  -ForegroundColor $Yel }
function Die([string]$m)  {
    Write-Host "[!] INSTALL FAILED: $m" -ForegroundColor $Red
    if (-not $env:GR_NONINTERACTIVE) { Read-Host "press Enter to close" }
    exit 1
}

# ── uninstall ──────────────────────────────────────────────────
if ($Uninstall) {
    Step "stopping + removing scheduled tasks"
    foreach ($t in "GOLD-REAPER","GOLD-REAPER-DASHBOARD","GOLD-REAPER-WATCHDOG","GOLD-REAPER-RETRAIN") {
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
    }
    $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Gold Reaper.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    Ok "tasks, shortcut and venv removed"
    Write-Host "  app dir   : $AppDir (kept - data + .env preserved)"
    if ($Purge) { Remove-Item $Share -Recurse -Force; Ok "PURGED $Share" }
    exit 0
}

# ── 1. OS + arch detect ────────────────────────────────────────
$Arch = $env:PROCESSOR_ARCHITECTURE
Step "windows ($Arch) detected"

# ── 2. python acquisition chain: py -3.12 -> winget ────────────
function Test-PyInRange([string]$exe) {
    if ([string]::IsNullOrWhiteSpace($exe) -or -not (Test-Path $exe)) { return $false }
    & $exe -c "import sys; sys.exit(0 if (3,10)<=(sys.version_info[0],sys.version_info[1])<=(3,12) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$PyExe = ""
Step "acquiring python 3.10-3.12 (validated, never blindly trusted)"
$candidates = @()
$pyLauncher = (Get-Command "py" -ErrorAction SilentlyContinue)
if ($pyLauncher) {
    & py -3.12 -c "import sys; print(sys.executable)" 2>$null | Tee-Object -Variable pyPath | Out-Null
    if ($LASTEXITCODE -eq 0 -and $pyPath) { $candidates += "$pyPath" }
}
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                 "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                 "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe")) {
    if (Test-Path $c) { $candidates += $c }
}
foreach ($c in $candidates) {
    if (Test-PyInRange $c) { $PyExe = $c; break }
}
if (-not $PyExe) {
    Step "python 3.12 not found -> winget install Python.Python.3.12"
    winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    # EXPLICIT exit-code check (the contract: never rely on try/catch here)
    if ($LASTEXITCODE -ne 0) {
        Die "winget failed with exit code $LASTEXITCODE.
        Install python 3.12 manually: https://www.python.org/downloads/
        (tick 'Add python.exe to PATH'), then re-run this installer."
    }
    $env:Path = "$env:Path;$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
    $cand = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-PyInRange $cand)) {
        Die "winget reported success but $cand is not a valid python 3.10-3.12.
        Install manually from https://www.python.org/downloads/ and re-run."
    }
    $PyExe = $cand
}
if (-not $PyExe) { Die "no python 3.10-3.12 available. See docs/TROUBLESHOOTING.md #1." }
Ok "python validated: $PyExe"

$PywExe = $PyExe -replace "python\.exe$", "pythonw.exe"
if (-not (Test-Path $PywExe)) { $PywExe = $PyExe }

# ── 3. stop old tasks (idempotency) ────────────────────────────
Step "stopping any previous GOLD-REAPER tasks (safe re-run)"
foreach ($t in "GOLD-REAPER","GOLD-REAPER-DASHBOARD","GOLD-REAPER-WATCHDOG") {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
}

# ── 4. copy app (preserve data/ + .env) ────────────────────────
Step "copying app -> $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir, $LogsDir | Out-Null
robocopy $Src $AppDir /MIR /XD .git .venv venv __pycache__ data logs installer/output /XF .env | Out-Null
if ($LASTEXITCODE -ge 8) { Die "robocopy failed (code $LASTEXITCODE)" }
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "data") | Out-Null
Ok "app in place (existing data/ + .env preserved)"

# ── 5. venv at project root + pinned install ───────────────────
Step "creating venv at $AppDir\.venv (project root)"
& $PyExe -m venv $Venv
if ($LASTEXITCODE -ne 0) { Die "venv creation failed (code $LASTEXITCODE)" }
$Vpip = Join-Path $Venv "Scripts\pip.exe"
& $Vpip install --upgrade pip -q
$Lock = Join-Path $AppDir "requirements.lock"
if (Test-Path $Lock) {
    Step "installing PINNED deps from requirements.lock (hash-verified)"
    & $Vpip install -r $Lock -q
    if ($LASTEXITCODE -ne 0) { Die "pinned dependency install failed (code $LASTEXITCODE)" }
} else {
    Warn "requirements.lock missing -> requirements-paper.txt (unpinned)"
    & $Vpip install -r (Join-Path $AppDir "requirements-paper.txt") -q
    if ($LASTEXITCODE -ne 0) { Die "dependency install failed (code $LASTEXITCODE)" }
}
Ok "paper stack installed"

$PyExeV  = Join-Path $Venv "Scripts\python.exe"
$PywExeV = Join-Path $Venv "Scripts\pythonw.exe"

if (-not (Test-Path (Join-Path $AppDir ".env"))) {
    Step "first-time setup wizard (broker / keys / risk)"
    Push-Location $AppDir
    & $PyExeV -m setup.wizard
    $wiz = $LASTEXITCODE
    Pop-Location
    if ($wiz -ne 0) { Warn "wizard skipped - runs at first launch (start_windows.bat)" }
    else { Ok ".env written + broker tested" }
} else {
    Ok ".env already exists (never overwritten)"
}

# ── 6. desktop shortcut ────────────────────────────────────────
Step "creating desktop shortcut"
$wsh = New-Object -ComObject WScript.Shell
$lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Gold Reaper.lnk"
$lnk = $wsh.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $AppDir "start_windows.bat"
$lnk.WorkingDirectory = $AppDir
$lnk.IconLocation = Join-Path $AppDir "assets\icon.ico"
$lnk.Save()
Ok "desktop shortcut: $lnkPath"

# ── 7. Task Scheduler (logon + boot, restart 60s) ──────────────
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
             -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
             -StartWhenAvailable -Hidden
$Triggers = @( (New-ScheduledTaskTrigger -AtLogOn), (New-ScheduledTaskTrigger -AtStartup) )
function Reg-Task($name, $args_) {
    $action = New-ScheduledTaskAction -Execute $PywExeV -Argument $args_ -WorkingDirectory $AppDir
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $Triggers `
        -Settings $Settings -Force | Out-Null
}
Reg-Task "GOLD-REAPER"           "bot.py"
Reg-Task "GOLD-REAPER-DASHBOARD" "dashboard\app.py"
Reg-Task "GOLD-REAPER-WATCHDOG"  "watchdog.py"
Ok "tasks registered (logon + boot, restart 60s)"

# ── 8. HEALTH CHECK GATE ───────────────────────────────────────
Step "running the installation health check (12 steps)"
Push-Location $AppDir
& $PyExeV installer\health_check.py
$gate = $LASTEXITCODE
Pop-Location
if ($gate -ne 0) {
    Die "health check did not pass (exit $gate). Nothing was started,
    nothing claims success. Fix the step above and re-run (idempotent)."
}

Step "starting tasks (gate passed)"
Start-ScheduledTask -TaskName "GOLD-REAPER-DASHBOARD" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "GOLD-REAPER"           -ErrorAction SilentlyContinue

# ── 9. SUCCESS (gate passed) ───────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
Write-Host " GOLD REAPER INSTALLED — health check passed" -ForegroundColor $Green
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
Write-Host "  app        : $AppDir"
Write-Host "  venv       : $Venv"
Write-Host "  dashboard  : http://localhost:8080  (auto at logon)"
Write-Host "  doctor     : $PyExeV $AppDir\cli.py doctor"
Write-Host "  uninstall  : .\install_windows.ps1 -Uninstall [-Purge]"
Write-Host ""
Write-Host "  RULE #1: PAPER_MODE=true for at least 2 weeks." -ForegroundColor $Red
Write-Host "  RULE #2: never risk money you cannot burn."    -ForegroundColor $Red
Write-Host "════════════════════════════════════════════════" -ForegroundColor $Green
if (-not $env:GR_NONINTERACTIVE) { Read-Host "press Enter to close" }
