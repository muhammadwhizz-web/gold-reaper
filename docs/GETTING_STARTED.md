# Getting started — GOLD//REAPER

One click, one wizard, one dashboard. This guide takes a non-technical
user from zero to a running gold bot in ten minutes — in **paper mode**,
which costs nothing and risks nothing. Every step described in words,
every command copy-pasteable.

If anything fails, take the exact error text to
[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md); the risk contract is
[docs/RISK.md](RISK.md).

═══════════════════════════════════════════════════════════════════

## What you get

The installer does everything: Python environment, dependencies, autostart,
watchdog, dashboard and the setup wizard.

- **Paper broker by default** — virtual balance, no account, no keys
- **Setup wizard** — pick a broker; it writes `.env` and tests the
  connection before finishing
- **Dashboard** — http://localhost:8080, opens in your browser automatically
- **Autostart + watchdog** — survives reboots, self-heals crashes; a
  top-level exception guard means it never exits on error
- **Server-side SL/TP** — stops and targets live on the broker's servers;
  open trades stay protected even if your machine dies
- **Logs + audit trail** — every decision recorded, daily log rotation
- **Desktop icon** — one double-click; optional tray LED (Open Dashboard /
  Stop Reaper)

Repo: https://github.com/muhammadwhizz-web/gold-reaper

═══════════════════════════════════════════════════════════════════

## Mission targets — read this first

The engine hunts two engineering targets, enforced by circuit breakers:

```text
session target   $20 per 4-hour block
daily target     $120 per day
daily loss cap   -3% of day-start equity (full stop, latched)
```

When a target is hit (`STOP_AFTER_TARGET`), the bot stops opening new
trades. When the loss cap is hit, it stops and stays stopped until you
explicitly reset it:

```bash
python bot.py --reset-breakers
```

**The honest part:** those figures are engineering targets enforced by
risk circuit breakers — not guarantees. No trading bot guarantees profit.
This software is educational, publishes losing periods alongside winning
ones, and leveraged gold can lose more than your deposit. You are
responsible for your capital. Full contract: [docs/RISK.md](RISK.md).

### The PAPER-first protocol (mandatory)

```text
1. run PAPER for 2 weeks        — the default; watch, don't touch
2. go live at the smallest size — 0.01 lot
3. after 1 month green          — raise risk to 1%
4. never above 2%               — RISK_PER_TRADE_PCT hard ceiling
```

Skipping these steps is the fastest way to lose money.

═══════════════════════════════════════════════════════════════════

## 1. Windows

### Install from the Releases page (recommended)

1. Open the releases page:
   https://github.com/muhammadwhizz-web/gold-reaper/releases

2. Under the latest release, click **GoldReaper-Setup.exe** to download it.
   Your browser may warn about the file type — choose **Keep**.

3. Double-click the downloaded file. A blue screen appears:

   > "Windows protected your PC"
   > Microsoft Defender SmartScreen prevented an unrecognized app from starting.

   This is the standard warning for unsigned installers. Click
   **More info**, then **Run anyway**.

4. The installer window appears: **"Gold Reaper Setup Wizard"**. Click
   **Next**, **Next**, **Install**; a progress bar fills while files land
   in `%LOCALAPPDATA%\GoldReaper\`.
5. On the final screen leave **Launch Gold Reaper** ticked, click
   **Finish**.

6. A terminal window opens with the **setup wizard**:

```text
GOLD//REAPER SETUP
select broker:
  [1] Paper  (recommended — virtual balance, no account)
  [2] Exness via MetaTrader 5   (Windows)
  [3] Bitget futures            (XAUT/USDT)
choice [1]:
```

   Type `1` and press Enter. The wizard writes `.env`, tests the broker
   connection, prints `OK`. Choose **Paper first** — always.

7. Your browser opens **http://localhost:8080** — the dashboard: dark
   console, equity card, green session LED, scrolling log tail.

8. There is a **Gold Reaper** icon on your Desktop and in the Start Menu.
   From now on, that icon is the bot.

### Where things live

```text
%LOCALAPPDATA%\GoldReaper\          install root
    venv\                           Python environment
    app\                            the bot code
    app\data\reaper.log             the log that matters
    app\data\audit.jsonl            append-only decision trail
    app\data\heartbeat.json         rewritten every 60s by the bot
    logs\                           rotated old logs
```

### Stop and start

Any of these stops the bot:

- Right-click the tray icon (the LED near the clock) → **Stop Reaper**.
- Close the bot's terminal window.
- Or in PowerShell:

```powershell
Stop-ScheduledTask  -TaskName GOLD-REAPER
Start-ScheduledTask -TaskName GOLD-REAPER    # start it again
```

### Uninstall

**Settings → Apps → Installed apps → Gold Reaper → Uninstall.** The
uninstaller removes the scheduled tasks it registered.

Manual removal, in PowerShell:

```powershell
Unregister-ScheduledTask -TaskName GOLD-REAPER* -Confirm:$false
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\GoldReaper"
```

### Alternative for technical users — one command

```powershell
git clone https://github.com/muhammadwhizz-web/gold-reaper.git
cd gold-reaper
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

Same result: files in `%LOCALAPPDATA%\GoldReaper\`, four scheduled tasks
(`GOLD-REAPER`, `GOLD-REAPER-DASHBOARD`, `GOLD-REAPER-WATCHDOG`,
`GOLD-REAPER-RETRAIN`), wizard at the end. Tasks trigger on logon + boot
and restart every 60s if a process dies.

═══════════════════════════════════════════════════════════════════

## 2. Linux

Supported out of the box: **Ubuntu 20.04 / 22.04 / 24.04, Debian 11 / 12
(apt), Fedora (dnf), Arch (pacman)** — the installer auto-detects the
distro. You need a terminal and internet.

### Install

```bash
git clone https://github.com/muhammadwhizz-web/gold-reaper.git && cd gold-reaper && chmod +x install_linux.sh && ./install_linux.sh
```

What the installer prints, roughly:

```text
[reaper] distro: ubuntu 24.04 (apt) — ok
[reaper] python 3.12 — ok
[reaper] venv + dependencies — ok
[reaper] install root: /home/you/.local/share/gold-reaper
[reaper] systemd units: gold-reaper gold-reaper-dashboard gold-reaper-watchdog
[reaper] retrain timer + linger — enabled, runs without login
[reaper] desktop entry installed — starting setup wizard
```

### The setup wizard

The wizard runs automatically at the end of the install:

```text
GOLD//REAPER SETUP
select broker:
  [1] Paper  (recommended — virtual balance, no account)
  [2] Exness via MetaTrader 5
  [3] Bitget futures (XAUT/USDT)
choice [1]:
```

- Option **2** is Windows-only; on Linux the bot automatically falls back
  to Bitget or Paper (see `FAILOVER_CHAIN` in `.env.example`).
- Option **3** asks for your Bitget API key / secret / passphrase and tests
  the connection before finishing.

Rerun the wizard any time:

```bash
cd ~/.local/share/gold-reaper/app
python -m setup.wizard
```

Headless (no questions asked):

```bash
python -m setup.wizard --non-interactive --broker paper
```

### First launch

Double-click the **Gold Reaper** icon on your desktop (or find it in your
application menu). A terminal window opens, the wizard confirms the broker
connection, and your browser opens **http://localhost:8080**.

### Where things live

```text
~/.local/share/gold-reaper/              install root
    venv/  app/  logs/
    app/data/reaper.log                  the log that matters
    logs/reaper.log                      symlink into app/data/reaper.log
    app/data/audit.jsonl                 decision trail
    app/data/heartbeat.json              rewritten every 60s
~/.local/share/applications/gold-reaper.desktop + ~/Desktop copy
~/.config/systemd/user/gold-reaper*.service        services
```

### Managing the bot

```bash
systemctl --user status  gold-reaper          # is it running?
systemctl --user stop    gold-reaper          # stop
systemctl --user start   gold-reaper          # start
systemctl --user restart gold-reaper          # restart
journalctl --user -u gold-reaper -f           # live log
```

Dashboard service: `gold-reaper-dashboard`; watchdog:
`gold-reaper-watchdog`; `gold-reaper-retrain.timer` re-fits the ML layer
on schedule. Linger keeps everything alive without an open session.

### Uninstall

```bash
systemctl --user disable --now gold-reaper gold-reaper-dashboard gold-reaper-watchdog
systemctl --user disable --now gold-reaper-retrain.timer
rm -f ~/.config/systemd/user/gold-reaper*.service
rm -f ~/.config/systemd/user/gold-reaper-retrain.timer
systemctl --user daemon-reload
rm -f ~/.local/share/applications/gold-reaper.desktop ~/Desktop/gold-reaper.desktop
rm -rf ~/.local/share/gold-reaper
```

═══════════════════════════════════════════════════════════════════

## 3. macOS

### Prerequisite — Homebrew

Open Terminal (Cmd+Space, type "Terminal", Enter). Install Homebrew if you
don't have it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

The first build also pulls the Xcode command-line tools — macOS offers
them; accept.

### Option A — script

```bash
git clone https://github.com/muhammadwhizz-web/gold-reaper.git
cd gold-reaper
chmod +x install_macos.sh && ./install_macos.sh
```

The wizard runs at the end — choose `1` (Paper). A LaunchAgent
(`com.goldreaper.bot`, KeepAlive) starts it at login and restarts on
death. While running, **Gold Reaper** shows in the Dock.

### Option B — dmg

1. Download **GoldReaper.dmg** from
   https://github.com/muhammadwhizz-web/gold-reaper/releases
2. Double-click it. A window opens showing the **Gold Reaper** app and an
   **Applications** folder shortcut.
3. Drag the app onto **Applications**.
4. First launch: right-click the app → **Open** → **Open**. This is the
   Gatekeeper bypass for unsigned apps — one time only. (Alternatively:
   System Settings → Privacy & Security → **Open Anyway**.)
5. The wizard runs, then the dashboard opens in your browser.

### Where things live

```text
/Applications/Gold Reaper.app                  the app
~/.local/share/gold-reaper/                    install root
    venv/  app/  logs/
    app/data/reaper.log                        the log that matters
~/Library/LaunchAgents/com.goldreaper.bot.plist  autostart agent
```

### Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.goldreaper.bot.plist
rm ~/Library/LaunchAgents/com.goldreaper.bot.plist
rm -rf "/Applications/Gold Reaper.app" ~/.local/share/gold-reaper
```

═══════════════════════════════════════════════════════════════════

## First-time recommendations

- **Stay in Paper for two weeks.** The wizard defaults to it; leave it
  there.
- **Do not edit `.env` by hand yet.** The wizard writes correct values;
  every key is documented in `.env.example`.
- **Turn on alerts.** Re-run the wizard with `--telegram-token` and
  `--telegram-chat` and the bot messages you on fills, breakers and
  restarts. Discord webhooks work too (`DISCORD_WEBHOOK_URL`).
- **Use an always-on machine.** A spare laptop, mini PC or cheap VPS. The
  watchdog restarts crashes; it cannot restart a machine that is off.
- **Check in once a day, not once a minute.** News blackouts, session
  guards and the weekend lock handle the hours that don't pay.
- **Read [docs/RISK.md](RISK.md)** before you even think about live mode.

### `.env` essentials (written by the wizard)

```text
BROKER / PAPER_MODE          broker choice, paper switch
MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_SYMBOL / MT5_PATH
BITGET_KEY / BITGET_SECRET / BITGET_PASSPHRASE / BITGET_SYMBOL / BITGET_LEVERAGE
STARTING_BALANCE             paper balance + sizing base
RISK_PER_TRADE_PCT           0.5–2.0 (protocol: start at the floor)
MAX_DAILY_LOSS_PCT           daily loss breaker
TARGET_PER_SESSION_USD       $20 per 4h block
TARGET_PER_DAY_USD           $120 per day
FAILOVER_CHAIN               e.g. MT5,BITGET,PAPER
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / DISCORD_WEBHOOK_URL
```

═══════════════════════════════════════════════════════════════════

## Verify it works

Run through this after installing. Every box should tick.

**1. The dashboard shows data.** Open http://localhost:8080 — equity card
populated, session LED lit, log tail scrolling. In paper mode equity
equals `STARTING_BALANCE` until the first fill.

**2. The log is alive.** Expect boot lines, broker connection, heartbeat
ticks:

```bash
# Linux / macOS
tail -f ~/.local/share/gold-reaper/app/data/reaper.log
```

```powershell
# Windows
Get-Content "$env:LOCALAPPDATA\GoldReaper\app\data\reaper.log" -Wait
```

**3. The heartbeat is fresh.** `data/heartbeat.json` is rewritten every
60s. Stale for 180s+ → the watchdog restarts the bot, by design.

**4. The desktop icon launches.** Double-click it: terminal, broker
check, dashboard. Stop it again from the tray (Windows) or commands above.

**5. The audit trail exists.** `data/audit.jsonl` — one JSON line per
decision. The black box; do not edit it.

**6. Weekend? Expect silence.** Gold does not trade Sat/Sun; the bot
idles with a warm heartbeat. Correct behavior.

═══════════════════════════════════════════════════════════════════

If any box refused to tick, take its exact symptom to
[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md).

**Disclaimer:** the $20/4h and $120/day figures are engineering targets
enforced by circuit breakers — not guarantees. No bot guarantees profit.
Educational software; you are responsible for your own capital.
