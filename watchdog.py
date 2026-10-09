#!/usr/bin/env python3
"""
GOLD REAPER :: watchdog
=======================
A separate lightweight process that keeps the bot alive even if the bot
cannot keep itself alive (hard crash, OOM-kill, terminal closed).

Mechanism:
  - the bot writes data/heartbeat.json every loop tick (~60s)
  - the watchdog reads it every WATCHDOG_INTERVAL (default 60s)
  - if the heartbeat is missing or stale for > WATCHDOG_STALE_SECONDS
    (default 180s) AND the market should be open, the watchdog restarts
    the bot through the platform's own supervisor:

      Linux   : systemctl --user restart gold-reaper   (if installed)
                else a detached `python bot.py`
      Windows : Start-ScheduledTask GOLD-REAPER        (if installed)
                else a detached `pythonw bot.py`
      macOS   : launchctl kickstart -k gui/<uid>/com.goldreaper.bot
                else a detached `python bot.py`

Usage:
  python watchdog.py                 # forever
  python watchdog.py --once          # single check (CI / cron)
  python watchdog.py --stale 240 --interval 30
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

HEARTBEAT = ROOT / "data" / "heartbeat.json"
WATCHLOG = ROOT / "data" / "watchdog.log"

if os.name == "nt":
    try:
        _out: Any = sys.stdout
        _out.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    try:
        print(line, flush=True)
        WATCHLOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCHLOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def heartbeat_age() -> float | None:
    """Seconds since the bot's last heartbeat, or None if unreadable."""
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        ts = float(hb.get("ts", 0))
        return max(0.0, time.time() - ts)
    except Exception:  # noqa: BLE001
        return None


def bot_pid_alive() -> bool:
    """If heartbeat carries a pid, verify that process still exists."""
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        pid = int(hb.get("pid", 0))
        if pid <= 0:
            return True  # unknown -> rely on staleness only
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True, timeout=10)
            return str(pid) in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # noqa: BLE001
        return True


# ───────────────────────────────────────────────────── platform restarts
def _systemctl_available() -> bool:
    if os.name != "posix" or os.uname().sysname == "Darwin":
        return False
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-enabled", "gold-reaper.service"],
            capture_output=True, text=True, timeout=10)
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _macos_service_exists() -> bool:
    try:
        uid = os.getuid()
        out = subprocess.run(["launchctl", "print", f"gui/{uid}/com.goldreaper.bot"],
                             capture_output=True, text=True, timeout=10)
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _task_exists() -> bool:
    try:
        out = subprocess.run(
            ["schtasks", "/Query", "/TN", "GOLD-REAPER"],
            capture_output=True, text=True, timeout=10)
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def restart_bot(python_exe: str) -> str:
    """Restart via the platform supervisor; fallback to detached process.

    Returns a human-readable description of what was done.
    """
    if _systemctl_available():
        subprocess.run(["systemctl", "--user", "restart", "gold-reaper.service"],
                       timeout=30, check=False)
        return "systemd: systemctl --user restart gold-reaper"
    if sys.platform == "darwin" and _macos_service_exists():
        subprocess.run(["launchctl", "kickstart", "-k",
                        f"gui/{os.getuid()}/com.goldreaper.bot"],
                       timeout=30, check=False)
        return "launchd: kickstart com.goldreaper.bot"
    if os.name == "nt" and _task_exists():
        subprocess.run(["schtasks", "/Run", "/TN", "GOLD-REAPER"],
                       timeout=30, check=False)
        return "taskscheduler: schtasks /Run GOLD-REAPER"

    # generic detached spawn — survives the watchdog, logs to data/
    bot = ROOT / "bot.py"
    out_log = open(ROOT / "data" / "watchdog_bot.out", "ab")
    if os.name == "nt":
        exe = str(Path(python_exe).with_name("pythonw.exe"))
        if not Path(exe).exists():
            exe = python_exe
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe, str(bot)], stdout=out_log, stderr=out_log,
                         cwd=str(ROOT), creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([python_exe, str(bot)], stdout=out_log, stderr=out_log,
                         cwd=str(ROOT), start_new_session=True, close_fds=True)
    return "spawned detached python bot.py"


def check(stale: float) -> tuple[str, str]:
    """One watchdog cycle. Returns (verdict, detail).

    verdict: OK | STALE | DEAD_PID | RESTARTED | MARKET_CLOSED
    """
    age = heartbeat_age()
    if age is None:
        # no heartbeat file at all: if a bot pid file exists but is dead
        # and the heartbeat never appeared, treat as stale
        if HEARTBEAT.exists() is False and not bot_pid_alive():
            detail = restart_bot(sys.executable)
            return "RESTARTED", f"no heartbeat, no live pid -> {detail}"
        return "OK", "no heartbeat yet (bot warming up or never ran)"
    if age <= stale:
        return "OK", f"heartbeat fresh ({age:.0f}s old)"
    if not bot_pid_alive():
        detail = restart_bot(sys.executable)
        return "RESTARTED", f"heartbeat stale {age:.0f}s > {stale:.0f}s + pid dead -> {detail}"
    # heartbeat stale but pid alive: bot wedged without crash — restart only
    # after 3x the stale window to avoid killing healthy slow ticks
    if age > stale * 3:
        detail = restart_bot(sys.executable)
        return "RESTARTED", f"heartbeat very stale {age:.0f}s (3x window), pid alive -> {detail}"
    return "STALE", f"heartbeat {age:.0f}s old (within grace)"


def main() -> int:
    ap = argparse.ArgumentParser(description="GOLD REAPER watchdog")
    ap.add_argument("--interval", type=float,
                    default=float(os.getenv("WATCHDOG_INTERVAL", "60")))
    ap.add_argument("--stale", type=float,
                    default=float(os.getenv("WATCHDOG_STALE_SECONDS", "180")))
    ap.add_argument("--once", action="store_true",
                    help="single check, exit 0 ok / 1 restarted-or-stale")
    args = ap.parse_args()

    log(f"watchdog online | interval={args.interval}s stale={args.stale}s "
        f"pid={os.getpid()}")
    verdict = "OK"
    while True:
        try:
            verdict, detail = check(args.stale)
            if verdict != "OK":
                log(f"[{verdict}] {detail}")
                if verdict == "RESTARTED":
                    try:
                        from core.notify import notify
                        notify("WATCHDOG", detail)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            log(f"watchdog error (never dies): {e}")
        if args.once:
            return 0 if verdict in ("OK", "STALE") else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
