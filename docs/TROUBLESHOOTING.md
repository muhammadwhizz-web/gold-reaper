# Troubleshooting — GOLD//REAPER

Read the log first — it names the problem in most cases:

```bash
tail -n 100 ~/.local/share/gold-reaper/app/data/reaper.log   # Linux/macOS
```

```powershell
Get-Content "$env:LOCALAPPDATA\GoldReaper\app\data\reaper.log" -Tail 100  # Windows
```

Companions: `journalctl --user -u gold-reaper -f` (live service log, Linux) ·
`data/audit.jsonl` (decisions) · `data/heartbeat.json` (the 60s pulse).

Format: **SYMPTOM** → **FIX**; quoted error text may vary slightly between
versions.

═══════════════════════════════════════════════════════════════════

## 1. Python not found or wrong version

Supported: **Python 3.10 – 3.12**.

**SYMPTOM**

```text
'python' is not recognized as an internal or external command   # Windows
python: command not found                                       # Linux/macOS
[reaper] python 3.13 found — need 3.10-3.12
```

**FIX — Windows:** the installer installs Python silently when missing; if
it still can't find it: `winget install -e --id Python.Python.3.12` (or
python.org installer, tick **Add python.exe to PATH**), then re-run.

**FIX — Linux:**

```bash
sudo apt install -y python3 python3-venv python3-pip   # Debian/Ubuntu
sudo dnf install -y python3 python3-pip                # Fedora
sudo pacman -S --needed python python-pip              # Arch
```

## 2. SmartScreen / Gatekeeper blocks the installer

**SYMPTOM — Windows:** blue screen, "Windows protected your PC — Microsoft
Defender SmartScreen prevented an unrecognized app from starting."

**FIX:** click **More info** → **Run anyway**. The installer is unsigned;
SmartScreen flags every unsigned download.

**SYMPTOM — macOS:** "Gold Reaper can't be opened because it is from an
unidentified developer" / "Apple cannot check it for malicious software."

**FIX:** right-click the app → **Open** → **Open** (one time only), or
System Settings → Privacy & Security → **Open Anyway**.

## 3. venv creation or pip install fails

**SYMPTOM**

```text
ensurepip is not available                      # venv module missing
error: externally-managed-environment           # distro-locked python
Could not fetch URL https://pypi.org/...        # no internet / proxy
```

**FIX:**

- Debian/Ubuntu missing venv module — install it, then re-run the
  installer: `sudo apt install -y python3-venv python3-pip`
- Corporate proxy — `export HTTPS_PROXY=http://proxy.company:8080` and
  `export HTTP_PROXY=http://proxy.company:8080` before installing
- No internet: fix the connection and re-run the installer. Both pip and
  the installer retry with exponential backoff, so transient outages
  self-resolve.

## 4. MetaTrader 5 (Exness)

### 4a. Terminal not installed
**SYMPTOM** — `MT5 terminal not found — install MetaTrader 5 or set MT5_PATH`
**FIX:** install the terminal from your broker (https://www.exness.com) or
official MT5 (https://www.metatrader5.com). Auto-detected:

```text
C:\Program Files\MetaTrader 5\terminal64.exe
C:\Program Files\Exness MetaTrader 5\terminal64.exe
```

Non-standard location → set `MT5_PATH=C:\MT5\terminal64.exe` in `.env`.

### 4b. Wrong credentials
**SYMPTOM** — `MT5 login failed` repeats; after 3 retries the bot sends an
alert (Telegram/Discord if configured) and walks the failover chain.
**FIX:** verify `MT5_LOGIN` (digits only), `MT5_PASSWORD`, `MT5_SERVER`.
The server string must match the terminal exactly, e.g.
`Exness-MT5Trial7` / `Exness-MT5Real5`. Fastest check: log in manually
in the MT5 terminal with the same values.

### 4c. Symbol not found
**SYMPTOM** — `symbol XAUUSD not found — trying XAUUSDm, XAUUSD.raw, GOLD`
**FIX:** the bot auto-tries the common broker gold names (`XAUUSDm`,
`XAUUSD.raw`, `GOLD`). If all fail, open Market Watch in the MT5 terminal,
copy the exact gold symbol your broker lists, and set
`MT5_SYMBOL=<that exact name>` in `.env`.

### 4d. "MetaTrader5 package not installed on this platform" (Linux/macOS)
**SYMPTOM** — that exact line in the log.
**FIX:** none needed. The MetaTrader5 Python package is Windows-only, so
this is expected on Linux/macOS. The bot logs it once and follows
`FAILOVER_CHAIN` (default `MT5 → BITGET → PAPER`), landing on Paper if
no other broker is configured. Not an error.

## 5. Bitget futures

### 5a. Invalid API key
**SYMPTOM** (wording varies)

```text
ccxt.AuthenticationError: bitget {"code":"40001","msg":"ACCESSKEY invalid"}
```

**FIX:** re-check `BITGET_KEY` / `BITGET_SECRET` / `BITGET_PASSPHRASE` in
`.env` — no quotes, no trailing spaces. The passphrase is shown **once**
at key creation; if lost, create a new API key. Or re-run the wizard.

### 5b. IP whitelist mismatch
**SYMPTOM** (wording varies — anything mentioning "white list" or "IP")

```text
bitget {"code":"40035","msg":"ip does not match white list"}
```

**FIX:** your public IP changed (reboot, new network, VPS migration). Find
it: `curl ifconfig.me`. Add it in Bitget → API management → edit key → IP
whitelist. Removing the whitelist also works but is less safe.

### 5c. Rate limit
**SYMPTOM** — `ccxt.RateLimitExceeded / DDoSProtection — backing off 4s`
**FIX:** nothing. The bot backs off exponentially — 1 → 2 → 4 → 8 → … →
60s max — and resumes automatically. Manual intervention makes it worse.

### 5d. Hedge vs one-way position mode
**SYMPTOM** — order rejected with a "position mode" / hedge-mode error.
**FIX:** set the Bitget account to **one-way** mode in futures
preferences — the bot manages one net position per symbol; one-way is
the tested path.

## 6. Dashboard won't open / port 8080 busy

**SYMPTOM — browser:** "This site can't be reached — localhost refused to
connect" at http://localhost:8080.

**FIX:** the dashboard service is down. Start it and read why:

```bash
Start-ScheduledTask -TaskName GOLD-REAPER-DASHBOARD   # Windows
systemctl --user start gold-reaper-dashboard          # Linux
journalctl --user -u gold-reaper-dashboard -f         # what's wrong
```

**SYMPTOM — log:** `OSError: [Errno 98] Address already in use`
(Linux/macOS) or `WinError 10048: Only one usage of each socket address` (Windows).

**FIX:** something else owns port 8080 — kill it or move the dashboard:

```bash
lsof -i :8080 && kill <PID>                             # Linux/macOS
netstat -ano | findstr :8080 ; taskkill /PID <PID> /F   # Windows
```

Or set `DASHBOARD_PORT=8081` in `.env` and restart the dashboard service.

## 7. Autostart not running

### Windows — Task Scheduler
**SYMPTOM:** after a reboot nothing runs; `Get-ScheduledTask GOLD-REAPER`
shows `Ready` instead of `Running`.
**FIX:**

```powershell
Start-ScheduledTask -TaskName GOLD-REAPER
Get-ScheduledTaskInfo -TaskName GOLD-REAPER   # LastRunTime, LastTaskResult
```

Task Scheduler GUI → task → **History** tab shows every trigger. Tasks
are configured to "Run whether user is logged on or not" with a 60s
restart; if you recreated them by hand, set that flag on the General tab.

### Linux — systemd
**SYMPTOM:** `Failed to connect to bus: No such file or directory`
**FIX:** you are in WSL without systemd. Add this to `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Then from PowerShell: `wsl --shutdown`, reopen the terminal, re-run the
installer's service step.

**SYMPTOM:** services stop when you log out.
**FIX:** enable linger so user units survive logout (the installer does
this; re-run it if linger was removed): `loginctl enable-linger $USER`

## 8. Bot keeps restarting

**SYMPTOM:** `journalctl --user -u gold-reaper` or the Windows task
history shows a restart every minute or two.

**FIX:** first, know the design. A top-level exception guard means the bot
never exits on error — it sleeps 30s and restarts its loop. The watchdog
pings every 60s and restarts the process only if `data/heartbeat.json` is
stale >180s. Restarts are the system working, not failing. Find the cause:
```bash
tail -n 200 ~/.local/share/gold-reaper/app/data/reaper.log
```

Look for the exception repeating before each restart. Usual suspects:
broker credentials (sections 4/5), corrupted `data/state.json` (section 12),
disk full (section 11).

**Not restarting — just not trading?** A breaker is latched (daily -3%,
loss streak, or a target hit); the bot stays up and idles by design. To
continue: `python bot.py --reset-breakers`

## 9. Market closed / weekend

**SYMPTOM:** no trades since Friday evening, session LED idle, heartbeat
alive, log mentions market closed.

**FIX:** nothing to fix. Gold does not trade Saturday/Sunday, and the bot
refuses entries within 2h of Friday close; the heartbeat stays warm. First
trades of the week usually appear in the London/NY overlap (12:00–16:00
UTC), not Monday 00:00 — that session is thin.

## 10. Clock drift warning

**SYMPTOM:** `WARN clock drift +2.4s — sync your system clock`

**FIX:** candle timestamps and broker fills depend on an accurate clock.

```bash
w32tm /resync                       # Windows — admin terminal
sudo timedatectl set-ntp true       # Linux → want "synchronized: yes"
sudo sntp -sS time.apple.com        # macOS
```

## 11. Disk space warning

**SYMPTOM:** `WARN low disk space: 0.8 GB free`

**FIX:** the bot wants at least 1GB free; old logs are the usual eater.
Logs rotate by size (2 MB, 30 rotations kept) — delete old rotations only:

```bash
rm ~/.local/share/gold-reaper/logs/reaper.log.*    # Linux/macOS
```

Windows: `Remove-Item "$env:LOCALAPPDATA\GoldReaper\logs\reaper.log.*"`.

Leave `data/audit.jsonl` alone — it is the decision record. Still tight?
The feature/retrain artifacts under `app/data/` are rebuildable by
re-running the ingest pipeline.

## 12. Starting over

### Fresh install (full reset)
1. Uninstall ([GETTING_STARTED.md](GETTING_STARTED.md) has per-OS steps).
2. Delete leftovers: `%LOCALAPPDATA%\GoldReaper\` (Windows) or
   `~/.local/share/gold-reaper/` (Linux/macOS).
3. Delete the repo clone, install again — the wizard starts clean.

### Reset paper state only
Stop the bot first, or it rewrites the files:

```bash
# stop: Windows tray → Stop Reaper, or Stop-ScheduledTask GOLD-REAPER
systemctl --user stop gold-reaper                      # Linux
rm ~/.local/share/gold-reaper/app/data/state.json      # equity/positions
rm ~/.local/share/gold-reaper/app/data/apex_risk.json  # breakers/trackers
# Windows equivalents under %LOCALAPPDATA%\GoldReaper\app\data\
```

Start again — paper balance returns to `STARTING_BALANCE`. Breakers
latched but keeping history? `python bot.py --reset-breakers` (section 8)
is the lighter tool.

## Still stuck

- Open an issue with the last 50 log lines + OS version:
  https://github.com/muhammadwhizz-web/gold-reaper/issues
- Install walkthrough: [docs/GETTING_STARTED.md](GETTING_STARTED.md)
- Risk contract — what the breakers actually do: [docs/RISK.md](RISK.md)

**Disclaimer:** the $20/4h and $120/day targets are engineering targets
enforced by circuit breakers — not guarantees. No trading bot guarantees
profit. Educational software. You are responsible for your own capital.
