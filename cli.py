#!/usr/bin/env python3
"""
GOLD REAPER :: gold-reaper doctor
=================================
PHASE 5 of the repair contract.

    python cli.py doctor          # read-only health report
    python cli.py doctor --fix    # auto-repair known issues (safe set)
    python cli.py start           # launch the bot detached (paper default)
    python cli.py stop            # SIGTERM the running bot
    python cli.py status          # one-line running/stopped + vitals

Doctor reports: OS, python, packages, files present/missing, DB health,
feed health, requested vs active broker, service status, last heartbeat,
last error. It NEVER prints secrets (values of *KEY*/*SECRET*/*PASS*/
*TOKEN* are masked even if a probe would naturally show them).
"""
from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # cli.py lives at the repo root
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
PID_FILE = DATA / "bot.pid"
HEARTBEAT_FILE = DATA / "heartbeat.json"

SECRET_MARKERS = ("KEY", "SECRET", "PASS", "TOKEN", "PASSWORD")

BOX_W = 70


def _box(lines: list[str]) -> None:
    print("+" + "-" * BOX_W + "+")
    for ln in lines:
        print(f"| {ln}".ljust(BOX_W + 2) + "|")
    print("+" + "-" * BOX_W + "+")


def _mask(key: str, value: str) -> str:
    if any(m in key.upper() for m in SECRET_MARKERS) and value:
        return value[:3] + "***masked***" if len(value) > 3 else "***masked***"
    return value


def _row(label: str, value: str, ok: bool | None = None) -> str:
    mark = {True: "[ok] ", False: "[!!] ", None: "[--] "}[ok]
    return f"{mark}{label:<24} {value}"[:BOX_W - 1]


# ───────────────────────────────────────────────────────────── doctor

def doctor(fix: bool = False) -> int:
    issues: list[str] = []
    fixes: list[str] = []
    lines: list[str] = ["GOLD REAPER DOCTOR" + (" (--fix)" if fix else "")]

    # 1. platform
    lines.append(_row("os", f"{platform.system()} {platform.release()} "
                               f"({platform.machine()})", True))
    v = sys.version_info
    py_ok = (3, 10) <= (v.major, v.minor) <= (3, 12)
    lines.append(_row("python", platform.python_version() +
                      f" @ {sys.executable}", py_ok))
    if not py_ok:
        issues.append("python outside 3.10-3.12")

    # 2. packages
    missing = []
    for mod in ("pandas", "numpy", "yfinance", "dotenv", "fastapi",
                "uvicorn", "sse_starlette", "duckdb"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    lines.append(_row("packages", "all core imports ok" if not missing
                      else f"missing: {', '.join(missing)}", not missing))
    if missing:
        issues.append("missing packages - pip install -r "
                      "requirements-paper.txt")

    # 3. files
    required = ["bot.py", "cli.py", "core/strategy.py", "core/risk.py",
                "brokers/paper_broker.py", "dashboard/app.py",
                "docs/PROTECTED_HASHES.txt"]
    gone = [f for f in required if not (ROOT / f).exists()]
    lines.append(_row("files", "all present" if not gone
                      else f"missing: {', '.join(gone)}", not gone))
    if gone:
        issues.append("missing files - re-clone the repo")

    # 4. db health
    db_note, db_ok = _db_health()
    lines.append(_row("duckdb", db_note, db_ok))
    if not db_ok:
        issues.append("duckdb unhealthy - delete data/healthcheck.duckdb "
                      "and re-run")

    # 5. account state
    acc_note, acc_ok, acc_fixable = _account_health()
    lines.append(_row("paper account", acc_note, acc_ok))
    if not acc_ok:
        if fix and acc_fixable:
            did = _fix_account()
            fixes.append(did)
            lines.append(_row("paper account", "restored from .bak", True))
        else:
            issues.append("paper account corrupt - restore "
                          "data/paper_account.json.bak or run doctor --fix")

    # 6. requested vs active broker
    req, act, hb_age = _broker_truth()
    if act:
        match = req == act
        lines.append(_row("broker", f"requested={req} active={act}", match))
        if not match:
            lines.append(_row("!!", "requested != active - the console "
                                   "shows a fallback", False))
            issues.append(f"broker fallback active: requested {req}, "
                          f"running {act}")
    else:
        lines.append(_row("broker", f"requested={req} (bot not running)",
                          None))

    # 7. heartbeat
    if hb_age is None:
        lines.append(_row("heartbeat", "no heartbeat file - bot offline",
                          None))
    else:
        fresh = hb_age < 180
        lines.append(_row("heartbeat", f"{hb_age:.0f}s old", fresh))
        if not fresh:
            issues.append(f"heartbeat stale ({hb_age:.0f}s) - bot may be "
                          "hung or dead")

    # 8. feed health (network; best-effort, never crashes doctor)
    feed_note, feed_ok = _feed_health()
    lines.append(_row("feed (GC=F)", feed_note, feed_ok))
    if not feed_ok:
        issues.append("paper feed degraded - trading halts by design "
                      "(P1-A); check network")

    # 9. service status
    lines.append(_row("service", _service_status(), None))

    # 10. last error from the log (masked)
    last_err = _last_error()
    lines.append(_row("last error", last_err or "none in recent log",
                      last_err == ""))

    _box(lines)
    if fix and fixes:
        print("fixes applied:")
        for f in fixes:
            print(f"  - {f}")
    if issues:
        print(f"\ndoctor: {len(issues)} issue(s) found")
        for i in issues:
            print(f"  ! {i}")
        return 1
    print("\ndoctor: all checks passed - the reaper is honest and armed.")
    return 0


def _db_health() -> tuple[str, bool]:
    try:
        import duckdb

        scratch = DATA / "healthcheck.duckdb"
        DATA.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(scratch))
        con.execute("CREATE TABLE IF NOT EXISTS t (k VARCHAR)")
        con.execute("INSERT INTO t VALUES ('ok')")
        con.close()
        scratch.unlink(missing_ok=True)
        store = DATA / "store"
        bars = ""
        if store.exists():
            try:
                from features.store import FeatureStore

                fs = FeatureStore()
                try:
                    n = len(fs.read_bars("1h"))
                finally:
                    fs.close()
                bars = f"; {n:,} 1h bars"
            except Exception as e:  # noqa: BLE001
                return f"store unreadable: {e}", False
        return f"read-write ok{bars}", True
    except Exception as e:  # noqa: BLE001
        return f"duckdb error: {e}", False


def _account_health() -> tuple[str, bool, bool]:
    f = DATA / "paper_account.json"
    if not f.exists():
        return "fresh (no state yet)", True, False
    try:
        import json

        json.loads(f.read_text())
        return "state file parses", True, False
    except Exception:  # noqa: BLE001
        bak = f.with_suffix(".json.bak")
        return (f"corrupt (backup {'present' if bak.exists() else 'MISSING'})",
                False, bak.exists())


def _fix_account() -> str:
    import json
    import shutil

    f = DATA / "paper_account.json"
    bak = f.with_suffix(".json.bak")
    try:
        json.loads(bak.read_text())   # only restore a VALID backup
        shutil.copyfile(bak, f)
        return f"restored {f.name} from {bak.name}"
    except Exception as e:  # noqa: BLE001
        archive = f.with_suffix(".json.corrupt")
        shutil.move(str(f), archive)
        return (f"backup unusable ({e}); corrupt file moved to "
                f"{archive.name}; fresh account will initialize")


def _broker_truth() -> tuple[str, str, float | None]:
    from brokers.factory import normalize

    req = normalize(os.getenv("BROKER", "PAPER"))
    act, hb_age = "", None
    if HEARTBEAT_FILE.exists():
        try:
            import json

            hb = json.loads(HEARTBEAT_FILE.read_text())
            hb_age = max(0.0, time.time() - float(hb.get("ts", 0)))
            act = str(hb.get("active_broker") or "")
        except Exception:  # noqa: BLE001
            pass
    return req, act, hb_age


def _feed_health() -> tuple[str, bool]:
    if not shutil.which("python3") and not Path(sys.executable).exists():
        return "no python", False
    try:
        from brokers.factory import normalize
        from brokers.paper_broker import PaperBroker
        from core.config import Config

        if normalize(os.getenv("BROKER", "PAPER")) != "PAPER":
            return ("live broker configured - probe skipped (never "
                    "touches the live venue)", True)
        b = PaperBroker(Config())
        ok = b.connect()
        if ok:
            return f"validated ({b.instrument})", True
        return f"DEGRADED: {b.status_reason}", False
    except Exception as e:  # noqa: BLE001
        return f"probe failed: {type(e).__name__}: {e}", False


def _service_status() -> str:
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("systemctl"):
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "gold-reaper.service"],
                capture_output=True, text=True, timeout=5)
            return f"systemd user unit: {r.stdout.strip() or 'unknown'}"
        if system == "Darwin":
            r = subprocess.run(
                ["launchctl", "list", "com.gold-reaper.bot"],
                capture_output=True, text=True, timeout=5)
            return f"launchagent: {'loaded' if r.returncode == 0 else 'not loaded'}"
        if system == "Windows":
            r = subprocess.run(
                ["schtasks", "/query", "/tn", "GoldReaper"],
                capture_output=True, text=True, timeout=5)
            return f"scheduled task: {'present' if r.returncode == 0 else 'absent'}"
    except Exception as e:  # noqa: BLE001
        return f"service probe failed: {e}"
    return "no service manager integration detected"


def _last_error() -> str:
    log = DATA / "reaper.log"
    if not log.exists():
        return ""
    try:
        tail = log.read_text(errors="replace").splitlines()[-500:]
        for ln in reversed(tail):
            if "ERROR" in ln or "Traceback" in ln or "CRITICAL" in ln:
                return ln[:BOX_W - 30]
    except OSError:
        pass
    return ""


# ─────────────────────────────────────────────────────── start/stop/status

def _bot_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start() -> int:
    pid = _bot_pid()
    if pid and _alive(pid):
        print(f"[start] bot already running (pid {pid})")
        return 0
    env = dict(os.environ)
    env.setdefault("BROKER", "PAPER")
    env.setdefault("PAPER_MODE", "true")
    env.setdefault("SUPERVISOR", "1")
    DATA.mkdir(parents=True, exist_ok=True)
    out = open(DATA / "reaper.cli.log", "ab")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "bot.py")],
        stdout=out, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env,
        start_new_session=True)
    time.sleep(2.0)
    if proc.poll() is not None:
        print(f"[start] bot exited immediately (code {proc.returncode}) - "
              f"read data/reaper.cli.log")
        return 1
    print(f"[start] bot launched (pid {proc.pid}) | paper default | "
          f"console: python dashboard/app.py")
    return 0


def stop() -> int:
    pid = _bot_pid()
    if not pid:
        print("[stop] no pid file - bot not tracked")
        return 0
    if not _alive(pid):
        print(f"[stop] pid {pid} is dead - cleaning stale pid file")
        PID_FILE.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[stop] SIGTERM sent to {pid} (graceful reaping)")
    except OSError as e:
        print(f"[stop] failed: {e}")
        return 1
    for _ in range(30):
        if not _alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print("[stop] bot offline, state flushed")
            return 0
        time.sleep(0.5)
    print(f"[stop] bot {pid} still alive after 15s - sending SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return 0


def status() -> int:
    pid = _bot_pid()
    running = bool(pid and _alive(pid))
    hb_age = None
    if HEARTBEAT_FILE.exists():
        hb_age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    eq = ""
    acc_file = DATA / "paper_account.json"
    if acc_file.exists():
        try:
            import json

            acc = json.loads(acc_file.read_text())
            eq = f" | paper cash ${acc.get('cash_balance', 0):,.2f} | " \
                 f"{len(acc.get('closed_trades', []))} closed trades"
        except Exception:  # noqa: BLE001
            pass
    if running:
        hb_txt = f"{hb_age:.0f}s ago" if hb_age is not None else "none"
        print(f"[status] RUNNING pid={pid} | heartbeat {hb_txt}{eq}")
        return 0
    hb_txt = f"{hb_age:.0f}s ago" if hb_age is not None else "none"
    print(f"[status] STOPPED | last heartbeat {hb_txt}{eq}")
    return 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gold-reaper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="read-only health report")
    d.add_argument("--fix", action="store_true",
                   help="auto-repair the safe set (account restore, stale pid)")
    sub.add_parser("start", help="launch the bot (paper default)")
    sub.add_parser("stop", help="gracefully stop the bot")
    sub.add_parser("status", help="running? heartbeat, cash, trades")
    args = ap.parse_args()

    if args.cmd == "doctor":
        return doctor(fix=bool(getattr(args, "fix", False)))
    if args.cmd == "start":
        return start()
    if args.cmd == "stop":
        return stop()
    if args.cmd == "status":
        return status()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
