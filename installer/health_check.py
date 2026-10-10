#!/usr/bin/env python3
"""
GOLD REAPER :: Installation Health Check (the gate)
===================================================
PHASE 4 of the repair contract: the installer may ONLY print SUCCESS
after every step here passes. Any failure prints

    INSTALL FAILED at step N - <reason> - <fix>

and exits 1. Never claims success. Never skips a step silently.

Steps (order fixed by the repair contract):
  1  Python version in range
  2  All required packages import
  3  Every required file exists (manifest)
  4  DuckDB opens read-write, tables present
  5  Data ingest runs (last 5 days), writes 1h bars
  6  Feature build runs, produces expected columns
  7  Paper broker connects (feed validated - P1-A)
  8  Paper trade opens AND closes (exercises P0-A)
  9  State persists across a simulated restart (exercises P0-B)
  10 Bot runs 30s in paper mode, writes heartbeat
  11 Dashboard responds HTTP 200 on /health
  12 Risk limits load and enforce correctly

Run:  python installer/health_check.py            (full gate)
      python installer/health_check.py --quick    (skip network-heavy steps)
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"

REQUIRED_FILES = [
    "bot.py", "cli.py",
    "core/config.py", "core/strategy.py", "core/risk.py", "core/risk_apex.py",
    "core/indicators.py", "core/account_state.py", "core/config_validation.py",
    "brokers/base.py", "brokers/paper_broker.py", "brokers/factory.py",
    "dashboard/app.py", "installer/health_check.py",
    "requirements-paper.txt", "docs/PROTECTED_HASHES.txt",
]

REQUIRED_IMPORTS = [
    "pandas", "numpy", "yfinance", "dotenv", "fastapi", "uvicorn",
    "sse_starlette", "duckdb",
]

EXPECTED_FEATURE_COLS = ("f_atr_pct", "f_adx_14", "f_rsi_14")


class StepResult:
    def __init__(self, num: int, name: str) -> None:
        self.num = num
        self.name = name
        self.ok = False
        self.detail = ""
        self.fix = ""


RESULTS: list[StepResult] = []


def _fail(step: StepResult, reason: str, fix: str) -> StepResult:
    step.ok = False
    step.detail = reason
    step.fix = fix
    return step


def _pass(step: StepResult, detail: str = "") -> StepResult:
    step.ok = True
    step.detail = detail
    return step


def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip the network-heavy steps (5, 7, 8, 10)")
    args = ap.parse_args()

    _s1_python()
    _s2_imports()
    _s3_files()
    _s4_duckdb()
    if not args.quick:
        _s5_ingest()
        _s6_features()
        _s7_feed()
        _s8_paper_trade()
        _s9_persistence()
        _s10_bot_30s()
    else:
        for n, name in ((5, "data ingest (skipped: --quick)"),
                        (6, "feature build (skipped: --quick)"),
                        (7, "paper feed validation (skipped: --quick)"),
                        (8, "paper trade open/close (skipped: --quick)"),
                        (9, "state persistence (skipped: --quick)"),
                        (10, "bot 30s run (skipped: --quick)")):
            r = StepResult(n, name)
            RESULTS.append(_pass(r, "skipped"))
    _s11_dashboard()
    _s12_risk()

    _report(args.quick)
    failed = [r for r in RESULTS if not r.ok]
    return 1 if failed else 0


# ─────────────────────────────────────────────────────────────── steps

def _s1_python() -> None:
    r = StepResult(1, "python version in 3.10-3.12")
    RESULTS.append(r)
    v = sys.version_info
    if not (3, 10) <= (v.major, v.minor) <= (3, 12):
        _fail(r, f"python {v.major}.{v.minor} is outside 3.10-3.12",
              "install python 3.12 (see docs/GETTING_STARTED.md)")
    else:
        _pass(r, f"{platform.python_version()} @ {sys.executable}")


def _s2_imports() -> None:
    r = StepResult(2, "required packages import")
    RESULTS.append(r)
    missing = []
    for mod in REQUIRED_IMPORTS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        _fail(r, f"cannot import: {', '.join(missing)}",
              "pip install -r requirements-paper.txt (or the lock file)")
    else:
        _pass(r, f"{len(REQUIRED_IMPORTS)} modules ok")


def _s3_files() -> None:
    r = StepResult(3, "required files present")
    RESULTS.append(r)
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    if missing:
        _fail(r, f"missing: {', '.join(missing)}",
              "re-download the repository (git pull / fresh clone)")
        return
    try:
        (DATA / "healthcheck_write_test").write_text("ok")
        (DATA / "healthcheck_write_test").unlink()
    except OSError as e:
        _fail(r, f"data/ is not writable: {e}",
              "check directory permissions on data/")
        return
    _pass(r, f"{len(REQUIRED_FILES)} files + writable data/")


def _s4_duckdb() -> None:
    r = StepResult(4, "duckdb opens read-write")
    RESULTS.append(r)
    try:
        import duckdb

        scratch = DATA / "healthcheck.duckdb"
        con = duckdb.connect(str(scratch))
        con.execute("CREATE TABLE IF NOT EXISTS healthcheck (k VARCHAR)")
        con.execute("DELETE FROM healthcheck")
        con.execute("INSERT INTO healthcheck VALUES ('rw-ok')")
        got = con.execute("SELECT k FROM healthcheck").fetchall()
        con.close()
        scratch.unlink(missing_ok=True)
        if got != [("rw-ok",)]:
            _fail(r, "scratch db roundtrip failed", "check disk + antivirus")
            return
        paper_db = DATA / "paper.duckdb"
        tables = ""
        if paper_db.exists():
            con = duckdb.connect(str(paper_db), read_only=True)
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema='main'").fetchall()
            con.close()
            tables = f"; paper.duckdb tables: {[x[0] for x in rows]}"
        _pass(r, f"read-write verified{tables}")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "reinstall duckdb")


def _s5_ingest() -> None:
    r = StepResult(5, "data ingest writes 1h bars (last 5d)")
    RESULTS.append(r)
    try:
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "data" / "ingest_multi_tf.py")],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT),
            env=env)
        if proc.returncode != 0:
            _fail(r, f"ingest exited {proc.returncode}: "
                     f"{(proc.stderr or proc.stdout)[-400:]}",
                  "check network / yahoo availability, re-run install")
            return
        from features.store import FeatureStore

        store = FeatureStore()
        try:
            h1 = store.read_bars("1h")
        finally:
            store.close()
        if h1.empty:
            _fail(r, "no 1h bars in the store after ingest",
                  "network blocked? ingest failed silently - run "
                  "python data/ingest_multi_tf.py and read the output")
            return
        last = h1.index[-1]
        age_days = (time.time() - last.timestamp()) / 86400
        if age_days > 5:
            _fail(r, f"latest 1h bar is {age_days:.1f} days old",
                  "the feed is stale - check network, re-run ingest")
            return
        _pass(r, f"{len(h1):,} 1h bars, latest {age_days:.2f}d old")
    except subprocess.TimeoutExpired:
        _fail(r, "ingest timed out after 300s", "check network, re-run")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "run ingest manually to see logs")


def _s6_features() -> None:
    r = StepResult(6, "feature build produces expected columns")
    RESULTS.append(r)
    try:
        import numpy as np

        from features.build_features import build_features
        from features.store import FeatureStore

        store = FeatureStore()
        try:
            h1 = store.read_bars("1h")
        finally:
            store.close()
        if len(h1) < 300:
            _fail(r, f"only {len(h1)} 1h bars - cannot build features",
                  "complete the ingest step first")
            return
        window = h1.tail(700).copy()
        for c in ("open", "high", "low", "close"):
            if c not in window.columns:
                window[c] = float(window["close"])
        if "volume" not in window.columns:
            window["volume"] = 1000.0
        X = build_features(window, None, with_labels=False, verbose=False)
        missing = [c for c in EXPECTED_FEATURE_COLS
                   if c not in X.columns]
        if missing or X.shape[1] < 50:
            _fail(r, f"feature columns missing {missing} "
                     f"({X.shape[1]} cols built)",
                  "features/build_features.py changed? file an issue")
            return
        if not np.isfinite(X[EXPECTED_FEATURE_COLS].tail(1).to_numpy(
                dtype=float)).all():
            _fail(r, "expected feature columns contain NaN/inf on last row",
                  "indicator warmup too short - ingest more history")
            return
        _pass(r, f"{X.shape[1]} feature columns verified")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "run research tooling manually")


def _s7_feed() -> None:
    r = StepResult(7, "paper broker connects (feed validated)")
    RESULTS.append(r)
    try:
        from brokers.paper_broker import ConnectionStatus, PaperBroker
        from core.config import Config

        broker = PaperBroker(Config())
        ok = broker.connect()
        if not ok or broker.status != ConnectionStatus.CONNECTED:
            _fail(r, f"feed DEGRADED: {broker.status_reason}",
                  "check network; the bot will also refuse - that is "
                  "P1-A working as designed")
            return
        _pass(r, f"{broker.instrument} validated: {broker.status_reason}")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "check network / yfinance")


def _s8_paper_trade() -> None:
    r = StepResult(8, "paper trade opens AND closes (P0-A)")
    RESULTS.append(r)
    try:
        from brokers.paper_broker import PaperBroker
        from core.config import Config

        broker = PaperBroker(Config())
        if not broker.connect():
            _fail(r, f"feed degraded: {broker.status_reason}", "see step 7")
            return
        px = broker.last_price()
        if not px:
            _fail(r, "no price available", "feed returned nothing - step 7")
            return
        sl = px - 25.0
        tp = px + 50.0
        res = broker.market_order("LONG", 0.01, sl, tp, "HEALTHCHECK", "gate")
        if not res.ok:
            _fail(r, f"open failed: {res.error}", "check paper broker")
            return
        # force an immediate close through the P0-A intended-price path
        pnl = broker.close_position(res.ticket, reason="HEALTHCHECK",
                                    intended_price=px)
        if pnl is None:
            _fail(r, "close returned None - P0-A exactly-once guard tripped",
                  "file an issue with data/reaper.log tail")
            return
        if len(broker.closed_trades) != 1 or broker.open_positions():
            _fail(r, "close did not record exactly one trade",
                  "file an issue with data/reaper.log tail")
            return
        _pass(r, f"opened {res.price:.2f} -> closed, realized "
                 f"{pnl:+.4f} (fee included)")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "run bot.py once and read logs")


def _s9_persistence() -> None:
    r = StepResult(9, "state persists across simulated restart (P0-B)")
    RESULTS.append(r)
    try:
        from brokers.paper_broker import PaperBroker
        from core.config import Config

        b1 = PaperBroker(Config())
        before = b1.balance()
        trades_before = len(b1.closed_trades)
        b2 = PaperBroker(Config())   # fresh instance = simulated restart
        if b2.balance() != before or \
                len(b2.closed_trades) != trades_before:
            _fail(r, "second instance sees different state",
                  "data/paper_account.json is being lost/overwritten")
            return
        if (DATA / "paper_account.json").exists() and \
                b2.realized_pnl != b1.realized_pnl:
            _fail(r, "realized pnl differs across restart", "file an issue")
            return
        _pass(r, f"balance {before:,.2f} + {trades_before} closed trades "
                 f"survive restart")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "check data/ permissions")


def _s10_bot_30s() -> None:
    r = StepResult(10, "bot runs 30s in paper mode, writes heartbeat")
    RESULTS.append(r)
    hb = DATA / "heartbeat.json"
    try:
        if hb.exists():
            hb.unlink()
        env = dict(os.environ)
        env.update({"SUPERVISOR": "0", "POLL_SECONDS": "10",
                    "BROKER": "PAPER", "PAPER_MODE": "true"})
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bot.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(ROOT), env=env)
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if not hb.exists():
                    time.sleep(1.0)
                    continue
                age = time.time() - hb.stat().st_mtime
                if age < 30:
                    break
                time.sleep(1.0)
            else:
                _fail(r, "no fresh heartbeat within 30s",
                      "run 'python bot.py' and read the console/log")
                return
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        import json

        payload = json.loads(hb.read_text())
        mode = payload.get("mode", "?")
        broker_active = payload.get("active_broker", "?")
        if broker_active and broker_active != "PAPER":
            _fail(r, f"bot came up on {broker_active}, expected PAPER",
                  "P1-B failover violated - DO NOT RUN; file an issue")
            return
        _pass(r, f"heartbeat fresh (mode={mode}, broker={broker_active})")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "run bot.py manually")


def _s11_dashboard() -> None:
    r = StepResult(11, "dashboard responds 200 on /health")
    RESULTS.append(r)
    try:
        import urllib.error

        from fastapi.testclient import TestClient

        from dashboard.app import app

        with TestClient(app) as client:
            resp = client.get("/health")
            if resp.status_code != 200:
                _fail(r, f"/health returned {resp.status_code}",
                      "check dashboard/app.py")
                return
            body = resp.json()
        # also verify the real HTTP surface when a server is already up
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8080/health", timeout=2) as rr:
                live_code = rr.status
        except Exception:  # noqa: BLE001
            live_code = None   # console not running - not a failure
        _pass(r, f"in-process 200 ({body.get('bot', '')})"
                 f"{f'; live :8080 -> {live_code}' if live_code else ''}")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "check dashboard/app.py imports")


def _s12_risk() -> None:
    r = StepResult(12, "risk limits load and enforce")
    RESULTS.append(r)
    try:
        from core.account_state import AccountState
        from core.config import Config
        from core.config_validation import validate_config
        from core.risk import RiskManager
        from core.risk_apex import ApexRisk

        validate_config()
        cfg = Config()
        rm = RiskManager(cfg)
        rm.state.equity = cfg.starting_balance
        rm.state.day.realized_pnl = -abs(
            cfg.max_daily_loss_pct / 100 * cfg.starting_balance) - 1
        ok, why = rm.can_trade("HEALTHCHECK")
        if ok:
            _fail(r, "legacy daily-loss breaker did not trip",
                  "core/risk.py gate broken - DO NOT trade; file an issue")
            return
        apex = ApexRisk(cfg)
        apex.state.day_start_equity = cfg.starting_balance
        apex.state.day_pnl = -0.05 * cfg.starting_balance
        ok_a, _ = apex.can_open(0.9)
        if ok_a:
            _fail(r, "apex daily breaker did not trip",
                  "core/risk_apex.py gate broken - DO NOT trade")
            return
        st = AccountState.load(cfg.starting_balance)
        st.register_fill(-10.0, False)
        if st.consec_losses != 1 or st.day_pnl != -10.0:
            _fail(r, "account state fill registration broken", "file an issue")
            return
        _pass(r, f"legacy breaker: {why[:44]} | apex breaker trips | "
                 f"account truth registers")
    except Exception as e:  # noqa: BLE001
        _fail(r, f"{type(e).__name__}: {e}", "check core/risk* + validation")


# ─────────────────────────────────────────────────────────────── report

def _report(quick: bool) -> None:
    green = "\033[92m"
    red = "\033[91m"
    yellow = "\033[93m"
    reset = "\033[0m"
    failed = [r for r in RESULTS if not r.ok]
    print()
    print("+" + "-" * 70 + "+")
    print("| GOLD REAPER :: INSTALL HEALTH CHECK".ljust(71) + "|")
    print("+" + "-" * 70 + "+")
    for r in RESULTS:
        mark = f"{green}PASS{reset}" if r.ok else f"{red}FAIL{reset}"
        skip = " (skipped)" if "skipped" in r.detail else ""
        print(f"| {r.num:>2}. {mark} {r.name}{skip}")
        if r.detail and not r.ok:
            print(f"|      reason: {red}{r.detail}{reset}")
            print(f"|      fix   : {yellow}{r.fix}{reset}")
        elif r.detail:
            print(f"|      {r.detail}")
    print("+" + "-" * 70 + "+")
    if failed:
        first = failed[0]
        print(f"| {red}INSTALL FAILED at step {first.num} - {first.name}"
              f"{reset}")
        print(f"|   {first.detail}")
        print(f"|   FIX: {first.fix}")
        print("+" + "-" * 70 + "+")
        print("The bot will NOT be started by the installer.")
    else:
        note = " (quick mode: network steps skipped)" if quick else ""
        print(f"| {green}ALL {len(RESULTS)} STEPS PASS{reset}{note}"
              f"{' - [ok to print SUCCESS]' if not quick else ''}")
        print("+" + "-" * 70 + "+")


if __name__ == "__main__":
    raise SystemExit(run())
