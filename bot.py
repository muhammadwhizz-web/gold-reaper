#!/usr/bin/env python3
"""
██╗  ██╗ █████╗ ██╗     ██╗
██║  ██║██╔══██╗██║     ██║
███████║███████║██║     ██║
██╔══██║██╔══██║██║     ██║
██║  ██║██║  ██║███████╗███████╗
╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝
GOLD REAPER APEX :: 24/7 XAU/USD autonomous hunter

APEX-X ensemble (regime-routed trend / meanrev / breakout / news modules
+ transparent ML soft vote) · $20/4h block engine · latched circuit
breakers · multi-broker failover · multi-account · audit trail · alerts.

Run:
  python bot.py                          # PAPER mode (default, safe)
  python bot.py --broker MT5             # force Exness MT5
  python bot.py --dashboard              # + live console on :8050
  python bot.py --reset-breakers         # clear latched circuit breakers
  APEX_ACCOUNTS="MT5|login|pass|srv;PAPER||" python bot.py   # multi-account
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

VERSION = "4.0.0"
HEARTBEAT_FILE = ROOT / "data" / "heartbeat.json"
PID_FILE = ROOT / "data" / "bot.pid"
STANDBY_FILE = ROOT / "data" / "standby.flag"
UPDATE_REPO = "muhammadwhizz-web/gold-reaper"


def standby_on() -> bool:
    """True while the dashboard kill-switch flag exists (entries paused).
    Exits/management keep running - only NEW entries are gated."""
    return STANDBY_FILE.exists()


def set_standby(on: bool, source: str = "dashboard") -> bool:
    """Write/remove the kill-switch flag. Returns the resulting state."""
    try:
        if on:
            STANDBY_FILE.parent.mkdir(parents=True, exist_ok=True)
            STANDBY_FILE.write_text(json.dumps({
                "on": True, "ts": time.time(), "source": source}),
                encoding="utf-8")
        else:
            STANDBY_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return standby_on()

import core.risk_apex as _risk_apex_mod  # noqa: E402  (late path binding)
from brokers.base import BrokerBase  # noqa: E402
from brokers.factory import (  # noqa: E402
    BrokerHaltError,
    connect_with_failover,
    create_broker,
    normalize,
)
from brokers.health import HealthMonitor  # noqa: E402
from brokers.paper_broker import PaperBroker  # noqa: E402
from core import audit  # noqa: E402
from core.account_state import AccountState, AccountStateError  # noqa: E402
from core.config import CONFIG, Config  # noqa: E402
from core.config_validation import ConfigError, validate_config  # noqa: E402
from core.indicators import atr as atr_fn  # noqa: E402
from core.logger import (  # noqa: E402
    GRAY,
    GREEN,
    RED,
    YELLOW,
    cprint,
    setup_logger,
)
from core.news_brain import NewsBrain  # noqa: E402
from core.notify import notify  # noqa: E402
from core.regime import RegimeDetector  # noqa: E402
from core.risk import RiskManager  # noqa: E402
from core.risk_apex import (  # noqa: E402
    BLOCK_TARGET,
    MAX_RISK_PCT,
    MIN_RISK_PCT,
    ApexRisk,
)
from core.sessions import (  # noqa: E402
    friday_cutoff_reached,
    human_local,
    is_market_open,
    minutes_until_market_close,
    now_utc,
    session_of,
)
from core.strategy import ReaperX  # noqa: E402
from core.strategy_apex import ApexX  # noqa: E402
from core.strategy_hpe import StrategyHPE  # noqa: E402
from features.build_features import build_features  # noqa: E402
from features.store import FeatureStore  # noqa: E402

log = setup_logger(CONFIG.log_file)


# ═════════════════════════════════════════════════════════════════ live feed


class LiveFeatureFeed:
    """Keeps a fresh feature row for the ensemble without re-fetching
    cross-asset data every minute. Core features recompute per closed bar;
    cross-asset block refreshes every 4h from the store (or network)."""

    def __init__(self, store: FeatureStore) -> None:
        self.store = store
        self._row: dict | None = None
        self._row_ts = None
        self._cross_row: dict = {}
        self._cross_ts = 0.0
        self._lock = threading.Lock()

    def _refresh_cross(self) -> None:
        try:
            feats = self.store.read_table("features_1h")
            if feats.empty:
                return
            last = feats.iloc[-1]
            self._cross_row = {c: (None if pd.isna(v) else float(v))
                               for c, v in last.items()
                               if c.startswith(("f_corr_", "f_beta_", "f_resid_z_",
                                                "f_coint_p_"))}
            self._cross_ts = time.time()
        except Exception:  # noqa: BLE001
            pass

    def current(self, h1: pd.DataFrame, ts) -> dict:
        with self._lock:
            if time.time() - self._cross_ts > 4 * 3600:
                self._refresh_cross()
            if self._row is not None and self._row_ts is not None and \
                    self._row_ts == h1.index[-1]:
                return self._row
            window = h1.tail(700).copy()
            try:
                X = build_features(window, None, with_labels=False, verbose=False)
                last = X.iloc[-1]
                row = {"ts": ts}
                for c in X.columns:
                    v = last[c]
                    row[c] = None if pd.isna(v) else float(v)
                # merge cached cross-asset dims (may be up to 4h stale - marked)
                for c, v in self._cross_row.items():
                    row.setdefault(c, v)
                self._row, self._row_ts = row, h1.index[-1]
            except Exception as e:  # noqa: BLE001
                log.warning("feature feed error: %s", e)
                if self._row is None:
                    self._row = {"ts": ts}
            return self._row


class MetaModelServer:
    """Serves the production meta-model probabilities (soft vote)."""

    def __init__(self) -> None:
        self.model = None
        self.columns: list[str] = []
        self.auc: float | None = None
        meta_p = ROOT / "ml" / "models" / "meta_production.json"
        model_p = ROOT / "ml" / "models" / "meta_production.joblib"
        if meta_p.exists() and model_p.exists():
            try:
                import joblib
                meta = json.loads(meta_p.read_text())
                if meta.get("test_auc", 0) >= 0.56:
                    art = joblib.load(model_p)
                    self.model, self.columns = art["model"], art["columns"]
                    self.auc = meta.get("test_auc")
                    log.info("meta-model loaded (engine=%s auc=%.3f)",
                             meta.get("engine"), self.auc)
            except Exception as e:  # noqa: BLE001
                log.warning("meta-model load failed: %s", e)

    def prob(self, row: dict) -> float | None:
        if self.model is None:
            return None
        try:
            x = np.array([[row.get(c) if row.get(c) is not None else 0.0
                           for c in self.columns]], dtype=np.float32)
            p = self.model.predict_proba(x)[0]
            return float(p[1]) if len(p) > 1 else float(p[0])
        except Exception:  # noqa: BLE001
            return None


def _guard_runtime_state() -> None:
    """Fail closed on corrupt risk-truth files (SD-10/SD-12 mitigation).

    core/risk.py and core/risk_apex.py are FROZEN and swallow parse errors
    (``load()`` except-pass, ``is_latched()`` except-pass).  A corrupt
    file must never silently become "fresh defaults" - the bot refuses to
    start instead, the same philosophy as PaperBroker/AccountState.
    Raises AccountStateError so main() maps it to exit code 3.
    """
    from core.config import CONFIG  # local: guard must see the same path
    # resolve files via module attributes at CALL time: construction-time
    # copies of these paths would follow re-pointed globals (the exact
    # atexit race class P0-B fixed) and break hermetic tests
    for path in (CONFIG.state_file, _risk_apex_mod.RISK_FILE,
                 _risk_apex_mod.BREAKER_FILE):
        if not path.exists():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise AccountStateError(
                f"runtime risk state {path} is corrupt ({e}). Restore or "
                f"remove it manually - the bot will not silently rebuild "
                f"risk truth.") from e


def _apex_state_stash() -> bytes | None:
    """Snapshot the persisted apex risk state (SD-9 mitigation, see below)."""
    risk_file = _risk_apex_mod.RISK_FILE
    try:
        return risk_file.read_bytes() if risk_file.exists() else None
    except OSError:
        return None


def _apex_state_restore(stash: bytes | None) -> None:
    """SD-9 mitigation (frozen core/risk_apex.py untouched).

    ``ApexRisk.__init__`` runs ``sync_period_starts()`` which ends in
    ``save()`` - BEFORE ``bot.start()`` ever calls ``load()``.  Every
    restart therefore overwrote the persisted state with defaults and
    weekly/monthly breaker memory was lost.  We snapshot the file before
    construction and restore it after; ``start()`` -> ``load()`` then
    re-reads the real history and rolls the day/week/month buckets
    correctly (its own sync_period_starts + save)."""
    if stash is None:
        return
    try:
        _risk_apex_mod.RISK_FILE.write_bytes(stash)
    except OSError as e:
        log.warning("apex state restore failed (continuing on defaults): %s", e)


def _append_equity_mark(equity: float, session: str | None) -> None:
    """Session-boundary equity snapshot (append-only CSV).

    Written once per session transition so the live equity curve gains real
    points between fills - the curve then shows the account drifting with
    realized equity instead of staying flat until a position closes.
    Honest by construction: only real broker equity at real timestamps.
    """
    if equity is None or equity <= 0:
        return
    f = ROOT / "data" / "equity_marks.csv"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        new = not f.exists()
        with f.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts", "equity", "session"])
            w.writerow([now_utc().isoformat(), f"{float(equity):.2f}",
                        session or ""])
    except OSError:
        log.warning("equity mark append failed", exc_info=True)


def _position_snapshot(broker) -> dict | None:
    """Open-position snapshot for the console's position card.
    None = flat. Mirrors the dashboard's state.position contract
    (side/size_oz/entry/sl/tp/opened/be) + trail/age_h extras."""
    try:
        pos = next(iter(broker.open_positions()), None)
        if pos is None:
            return None
        opened = pos.opened_at
        if opened.tzinfo is None:  # defensive: naive timestamps -> UTC
            opened = opened.replace(tzinfo=timezone.utc)
        return {
            "side": str(pos.side).upper(),
            "size_oz": round(float(pos.size_oz), 3),
            "entry": round(float(pos.entry), 2),
            "sl": round(float(pos.sl), 2),
            "tp": round(float(pos.tp), 2),
            "opened": opened.strftime("%H:%M:%S UTC"),
            "be": bool(pos.be_moved),
            "trail": bool(pos.trail_active),
            "age_h": round(max(0.0, (datetime.now(timezone.utc) - opened)
                               .total_seconds() / 3600.0), 1),
        }
    except Exception:  # noqa: BLE001 — snapshot must never kill the loop
        return None


def _write_heartbeat(mode: str, equity: float | None,
                     price: float | None = None,
                     regime: dict | None = None,
                     session: str | None = None,
                     position: dict | None = None,
                     broker_info: dict | None = None) -> None:
    """Liveness file for watchdog.py, the tray and the dashboard console.
    Carries the last classified telemetry so the console never has to
    fabricate regime/broker state while the bot is silent. broker_info
    carries the requested-vs-active distinction (P1-B)."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "version": VERSION,
            "mode": mode,
            "equity": equity,
            "price": price,
            "regime": regime,
            "session": session,
            "position": position,
            "standby": standby_on(),
            "requested_broker": (broker_info or {}).get("requested"),
            "active_broker": (broker_info or {}).get("active"),
            "broker_label": (broker_info or {}).get("label"),
        }), encoding="utf-8")
        tmp.replace(HEARTBEAT_FILE)
    except Exception:  # noqa: BLE001
        pass


# ═════════════════════════════════════════════════════════════════ the bot


class ReaperApexBot:
    def __init__(self, cfg: Config, account_label: str = "default") -> None:
        self.cfg = cfg
        self.label = account_label
        self.strategy: Any = ReaperX(cfg)   # ReaperX | StrategyHPE (HPE mode)
        # HPE mode (HPE Phases 3/5/7): opt-in via REAPER_STRATEGY=HPE. Default
        # stays APEX. HPE starts DISARMED (shadow telemetry, no orders) and
        # only arms when BOTH research artifacts say the gate passed.
        self._hpe: StrategyHPE | None = None
        self._hpe_armed = False
        self._hpe_stats = {"decisions": 0, "would_trade": 0, "vetoed": 0,
                           "alignment_max": 0}
        self._hpe_hist = {k: 0 for k in ("0.0-0.2", "0.2-0.4", "0.4-0.6",
                                         "0.6-0.8", "0.8-1.0")}
        self._hpe_matrix: dict = {}
        if os.environ.get("REAPER_STRATEGY", "").upper() == "HPE":
            from core.strategy_hpe import StrategyHPE
            self._hpe = StrategyHPE()
            self.strategy = self._hpe        # exits use HPE geometry
            self._hpe_armed = self._hpe_check_armed()
            mode = "ARMED" if self._hpe_armed else \
                "SHADOW - telemetry only, no orders"
            cprint(f"[*] strategy: HPE ({mode})", GREEN if self._hpe_armed
                   else YELLOW)
        self.risk = RiskManager(cfg)               # legacy state (sessions/day)
        # SD-9 mitigation: snapshot apex state BEFORE construction (the
        # frozen constructor saves defaults over the file) and restore after.
        _apex_stash = _apex_state_stash()
        self.apex = ApexRisk(cfg)                  # APEX survival layer
        _apex_state_restore(_apex_stash)
        # P0-C: ONE canonical money-truth, hydrated into both risk layers
        try:
            self.account = AccountState.load(cfg.starting_balance)
        except AccountStateError as e:
            cprint(f"[!] {e}", RED)
            raise
        self.requested_broker = normalize(cfg.broker)
        self.active_broker = self.requested_broker
        self._last_state_save = 0.0
        self.broker: BrokerBase = PaperBroker(cfg)
        self.running = True
        self._last_heartbeat = 0.0
        self._last_bar_ts = None
        self._store: FeatureStore | None = None
        self._feed: LiveFeatureFeed | None = None
        self._news: NewsBrain | None = None
        self._regime = RegimeDetector()
        self._meta = MetaModelServer()
        self._apexx: ApexX | None = None
        self._last_calendar_pull = 0.0
        self._health: HealthMonitor | None = None
        self._last_maintenance = 0.0
        self._last_update_check = 0.0
        self._last_equity: float | None = None
        self._last_price: float | None = None
        self._last_regime: dict | None = None
        self._last_session: str | None = None
        self._last_mark_session: str | None = None
        self._mode = "PAPER" if (getattr(cfg, "paper", False)
                                 or cfg.broker == "PAPER") else cfg.broker

    # ------------------------------------------------------------ lifecycle
    def build_broker(self, choice: str) -> BrokerBase:
        """Kept for compatibility; delegates to the universal factory."""
        return create_broker(self.cfg, choice)

    def _failover_chain(self) -> list[str]:
        primary = self.cfg.broker
        if getattr(self.cfg, "paper", False) or primary == "PAPER":
            return ["PAPER"]
        chain = [primary]
        if primary != "MT5":
            chain.append("MT5")
        if primary != "BITGET":
            chain.append("BITGET")
        chain.append("PAPER")
        return chain

    def start(self) -> int:
        from core.logger import print_banner
        print_banner(VERSION)
        log.info("APEX starting | account=%s broker=%s", self.label, self.cfg.broker)
        cprint(f"[*] account        : {self.label}", YELLOW)
        cprint(f"[*] mission        : ${BLOCK_TARGET:.0f}/4h block · "
               f"targets enforced by circuit breakers", YELLOW)
        cprint(f"[*] risk engine    : adaptive "
               f"{MIN_RISK_PCT}-{MAX_RISK_PCT}% · "
               f"day -3% / week -7% / month -15% latched stops", YELLOW)
        cprint(f"[*] local time     : {human_local(tz_name='Asia/Karachi')}", YELLOW)

        self.risk.load()
        self.apex.load()
        self._hydrate_risk_from_account()   # P0-C: one truth on restart
        self._store = FeatureStore()
        self._feed = LiveFeatureFeed(self._store)
        self._news = NewsBrain(self._store, self.cfg)
        self._apexx = ApexX(self.cfg, ml_auc=self._meta.auc)

        # P1-B: connect the REQUESTED broker or halt - never swap silently
        try:
            self.broker, tried = connect_with_failover(self.cfg, log)
        except BrokerHaltError as e:
            cprint("+" + "-" * 66 + "+", RED)
            cprint(f"| BROKER HALT: {e}".ljust(67) + "|", RED)
            cprint("|" + " " * 66 + "|", RED)
            cprint("| live->paper fallback is FORBIDDEN. Fix .env manually "
                   "or set".ljust(67) + "|", RED)
            cprint("| BROKER=PAPER for honest paper trading.".ljust(67) + "|", RED)
            cprint("+" + "-" * 66 + "+", RED)
            audit.log_event("lifecycle", {"event": "broker_halt",
                                          "requested": self.requested_broker})
            return 3
        self.active_broker = getattr(self.broker, "name", self.requested_broker)
        for kind in tried:
            cprint(f"[*] tried broker  : {kind}", GRAY)
        cprint(f"[*] active broker : {type(self.broker).name}", YELLOW)
        cprint(f"[*] broker mode   : {self._broker_label()}", YELLOW)
        if self.active_broker != self.requested_broker:
            # defensive: factory forbids this path, but never hide it
            log.warning("broker mismatch: requested=%s active=%s",
                        self.requested_broker, self.active_broker)
            notify("BROKER MISMATCH",
                   f"requested {self.requested_broker} but connected "
                   f"{self.active_broker} - inspect immediately")

        # 24/7 resilience: broker health probes + pid file (watchdog/tray)
        if not isinstance(self.broker, PaperBroker) and \
                hasattr(self.broker, "ensure_connected"):
            self._health = HealthMonitor(self.broker, self.cfg, log=log)
            self._health.start()
        try:
            PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        # regime detector warmup
        try:
            h1 = self._store.read_bars("1h")
            if len(h1) > 600:
                ok = self._regime.fit(h1.tail(4000))
                cprint(f"[*] regime engine  : "
                       f"{'HMM fitted' if ok else 'rule-based fallback'}", YELLOW)
        except Exception as e:  # noqa: BLE001
            log.warning("regime warmup skipped: %s", e)

        if self.apex.is_latched()[0]:
            cprint(f"[!] CIRCUIT BREAKER LATCHED: {self.apex.is_latched()[1]} "
                   f"— bot.py --reset-breakers to clear", RED)

        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        cprint("[✓] APEX ONLINE. hunting...", GREEN)
        # Liveness heartbeat BEFORE the first tick. The first tick may include
        # a calendar network pull (up to its full timeout) and supervisors,
        # watchdogs and the install health gate watch heartbeat.json for
        # liveness - a slow feed must never look like a dead bot.
        # (installer-e2e caught this: windows-2022 step 10 failed with
        #  'no fresh heartbeat within 30s' because the first tick blocked on
        #  the calendar pull and the gate deadline was exactly 30s.)
        _write_heartbeat(self._mode, self._last_equity,
                         price=self._last_price,
                         regime=self._last_regime,
                         session=self._last_session,
                         position=_position_snapshot(self.broker),
                         broker_info={
                             "requested": self.requested_broker,
                             "active": self.active_broker,
                             "label": self._broker_label(),
                         })
        audit.log_event("lifecycle", {"event": "start", "account": self.label,
                                      "broker": type(self.broker).name})

        exit_code = 0
        try:
            while self.running:
                self.tick()
                _write_heartbeat(self._mode, self._last_equity,
                                 price=self._last_price,
                                 regime=self._last_regime,
                                 session=self._last_session,
                                 position=_position_snapshot(self.broker),
                                 broker_info={
                                     "requested": self.requested_broker,
                                     "active": self.active_broker,
                                     "label": self._broker_label(),
                                 })
                self._maintenance()
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("fatal loop error: %s", e)
            notify("CRASH", f"fatal loop error: {e}")
            exit_code = 1
        finally:
            self.shutdown()
        return exit_code

    def _stop(self, *_args) -> None:
        cprint("\n[!] shutdown signal received. reaping quietly...", YELLOW)
        self.running = False

    # ------------------------------------------------------------ P0-C
    def _broker_label(self) -> str:
        """Honest broker badge: live vs paper, fallback never silent."""
        live = self.active_broker in ("MT5", "BITGET")
        if self.active_broker == self.requested_broker:
            return f"BROKER: {self.active_broker} " \
                   f"({'LIVE' if live else 'PAPER'})"
        return (f"BROKER: {self.active_broker} "
                f"(fallback from {self.requested_broker})")

    def _hydrate_risk_from_account(self) -> None:
        """P0-C: push canonical money-truth into BOTH risk layers so their
        gates always decide on identical inputs (modules stay frozen)."""
        st = self.account
        self.risk.state.balance = st.balance
        self.risk.state.equity = st.equity
        self.apex.state.balance = st.balance
        self.apex.state.equity = st.equity
        self.apex.state.day_pnl = st.day_pnl
        self.apex.state.consec_losses = st.consec_losses
        if st.day_date:
            self.apex.state.day_date = st.day_date

    def _paper_heartbeat_save(self) -> None:
        """P0-B: flush paper account every 60s while hunting."""
        if isinstance(self.broker, PaperBroker) and \
                time.time() - self._last_state_save >= 60:
            self._last_state_save = time.time()
            self.broker.heartbeat_save()

    def shutdown(self) -> None:
        try:
            for p in self.broker.open_positions():
                log.info("open position on exit: %s %s %.2f oz @ %.2f",
                         p.ticket, p.side, p.size_oz, p.entry)
            if self._health:
                self._health.stop()
            self.risk.save()
            self.apex.save()
            self.broker.disconnect()
            if self._store is not None:
                self._store.close()
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            cprint(f"[*] day pnl: {self.apex.state.day_pnl:+.2f} USD | "
                   f"balance {self.apex.state.balance:,.2f}", GREEN)
            cprint("[✓] APEX offline. state saved.", GRAY)
        except Exception:  # noqa: BLE001
            log.exception("shutdown error")

    # ------------------------------------------------------------ main tick
    def tick(self) -> None:
        self.risk.rollover_day()
        self.apex.sync_period_starts()
        self._paper_heartbeat_save()          # P0-B: 60s state flush
        ts = now_utc()

        # refresh the calendar every 30 min (news dimension)
        if time.time() - self._last_calendar_pull > 1800:
            self._last_calendar_pull = time.time()
            try:
                from data.news_ingest import load_calendar, normalize
                df = normalize(load_calendar())
                if not df.empty and self._store is not None:
                    self._store.write_table("calendar", df, replace=True)
            except Exception as e:  # noqa: BLE001
                log.warning("calendar refresh failed: %s", e)

        if not is_market_open(ts):
            self._heartbeat("market closed (weekend)")
            return

        data = self.broker.candles("h1", 800)
        h1: pd.DataFrame = data["h1"]
        h4: pd.DataFrame = data["h4"]
        if h1 is None or len(h1) < 260 or h4 is None or len(h4) < 30:
            self._heartbeat("warming up: not enough candles")
            return
        try:
            self._last_price = float(h1["close"].iloc[-1])
            self._last_session = session_of(ts)
        except (TypeError, ValueError, KeyError):
            pass
        pd_td = pd.Timedelta("1h")
        h1_closed = h1.iloc[:-1] if h1.index[-1] > ts - pd_td else h1
        last_bar = h1_closed.index[-1]
        if self._last_bar_ts == last_bar:
            self._manage_positions(h1)
            return
        self._last_bar_ts = last_bar

        # equity sync (P0-C: canonical state first, then hydrate both layers)
        try:
            eq = self.broker.equity()
            if eq is not None and eq > 0:
                self._last_equity = eq
                self.account.sync_equity(eq)
                self._hydrate_risk_from_account()
                self.risk.update_equity(eq)
                # session-boundary mark: one snapshot per session transition
                sess = session_of(ts)
                if sess != self._last_mark_session:
                    self._last_mark_session = sess
                    _append_equity_mark(eq, sess)
        except Exception:  # noqa: BLE001
            pass

        # manage exits first
        self._manage_positions(h1_closed)

        # weekend guards
        if friday_cutoff_reached(ts, self.cfg.friday_last_entry_utc):
            self._heartbeat("friday cutoff - no new entries")
            return
        if minutes_until_market_close(ts) < \
                self.cfg.no_entry_minutes_before_weekend_close and ts.weekday() == 4:
            self._heartbeat("pre-weekend-close blackout")
            return

        # dashboard kill-switch: exits above still ran; entries stop here
        if standby_on():
            self._heartbeat("standby flag set - entries paused (kill-switch)")
            return

        # ── APEX dimension assembly ─────────────────────────────────
        if self._feed is None or self._news is None or self._apexx is None:
            return  # components not initialised
        feat_row = self._feed.current(h1_closed, ts)
        regime = self._regime.classify(h1_closed.tail(600))
        self._last_regime = regime.to_dict()
        news_state = self._news.state(ts)
        ml_prob = self._meta.prob(feat_row)

        # strategy-level gates first (cheap)
        if getattr(__import__("core.sessions", fromlist=["in_blackout"]),
                   "in_blackout")(ts, self.cfg.blackout_hours_utc):
            self._heartbeat("US-data blackout window")
            return

        # ensemble decision (HPE mode replaces the APEX evaluator; same
        # broker-agnostic Signal interface, geometry and exit manager)
        sig: Any = None
        if self._hpe is not None:
            sig = self._hpe.evaluate(h1_closed, h4, ts,
                                     news=news_state.to_dict(),
                                     ml_prob=ml_prob)
            self._hpe_bookkeep(sig, regime)
            if sig is not None and not self._hpe_armed:
                audit.log_signal(sig, regime, news_state, {}, ml_prob,
                                 "HPE_SHADOW", "disarmed - telemetry only, "
                                 "no walk-forward evidence of edge")
                notify("HPE SHADOW SIGNAL",
                       f"{sig.side} conf {sig.meta.get('confidence', 0):.2f} "
                       f"- disarmed, no order placed")
                sig = None
        else:
            sig = self._apexx.evaluate(h1_closed, h4, ts, feat_row, regime,
                                       news_state, ml_prob)
        if sig is None:
            trace = getattr(self._apexx, "_last_trace", [])
            votes = getattr(self._apexx, "_last_votes", {})
            self._heartbeat(f"{sess_str(ts)}: no kill "
                            f"(regime {regime.regime}, votes {votes or '∅'})")
            audit.log_event("skip", {
                "regime": regime.to_dict(), "news": news_state.to_dict(),
                "votes": votes, "ml_prob": ml_prob,
                "trace": trace[-4:] if trace else []})
            return

        # APEX risk gates
        allowed, why = self.apex.can_open(sig.confidence)
        if not allowed:
            audit.log_signal(sig, regime, news_state,
                             sig.votes, ml_prob, "VETOED_RISK", why)
            self._heartbeat(why)
            return

        self._execute(sig, ml_prob, regime, news_state)

    # ------------------------------------------------------------ execution
    def _execute(self, sig, ml_prob, regime, news_state) -> None:
        open_pos = self.broker.open_positions()
        if len(open_pos) >= self.cfg.max_open_trades:
            return
        oz, risk_usd = self.apex.position_size_oz(sig.entry, sig.sl)
        if oz <= 0:
            log.warning("position size 0 - skip")
            return
        res = self.broker.market_order(sig.side, oz, sig.sl, sig.tp,
                                       sig.session, sig.reasoning[-1])
        if res.ok:
            audit.log_signal(sig, regime, news_state, sig.votes, ml_prob,
                             "EXECUTED", f"oz={oz} risk=${risk_usd:.2f}")
            self._journal(sig, oz, res.price or sig.entry, risk_usd)
            cprint(f"[>>>] APEX KILL {sig.side} {oz:.3f} oz @ "
                   f"{(res.price or sig.entry):.2f} | conf {sig.confidence:.2f} "
                   f"| regime {sig.regime} | votes {sig.votes}", YELLOW)
            notify("KILL ORDER",
                   f"{sig.side} XAUUSD {oz:.3f} oz @ {res.price or sig.entry:.2f}\n"
                   f"conf {sig.confidence:.2f} · regime {sig.regime}\n"
                   f"votes: {sig.votes} · reasoning: {sig.reasoning[-1]}")
        else:
            audit.log_event("order", {"ok": False, "error": res.error})
            log.warning("order rejected: %s", res.error)

    def _journal(self, sig, size: float, price: float, risk_usd: float) -> None:
        f = self.cfg.trades_file
        new = not f.exists()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts_utc", "side", "size_oz", "entry", "sl", "tp",
                            "session", "risk_oz_atr", "risk_usd", "regime",
                            "confidence", "votes"])
            w.writerow([datetime.now(timezone.utc).isoformat(), sig.side, size,
                        price, sig.sl, sig.tp, sig.session, sig.atr, risk_usd,
                        sig.regime, sig.confidence,
                        "|".join(f"{k}:{v}" for k, v in sig.votes.items())])

    # ------------------------------------------------------------ HPE shadow
    def _hpe_check_armed(self) -> bool:
        """HPE only arms when BOTH research gates passed. Neither is true
        today (ml AUC < gate, ensemble zero qualifying trades) - so the
        default is honest shadow telemetry."""
        try:
            ml_meta = json.loads((ROOT / "ml" / "models" / "hpe_production.json")
                                 .read_text())
            bt_meta = json.loads((ROOT / "data" / "hpe_report.json").read_text())
            return bool(ml_meta.get("armed") and bt_meta.get("armed"))
        except Exception:  # noqa: BLE001
            return False

    def _hpe_bookkeep(self, sig, regime) -> None:
        """Shadow telemetry: every in-window HPE decision feeds the dashboard
        state file (data/hpe_state.json). Explain mode logs accepted signals.
        Never raises - telemetry must never kill the trading loop."""
        hpe = self._hpe
        if hpe is None:
            return
        d = hpe.state.last_decision
        if d is None:
            return
        st = self._hpe_stats
        st["decisions"] += 1
        st["alignment_max"] = max(st["alignment_max"], d.aligned)
        if d.accepted:
            st["would_trade"] += 1
            from ml.explain_hpe import format_decision
            cprint("[HPE] signal explanation:", YELLOW)
            for line in format_decision(d.to_dict()):
                cprint(f"  {line}", YELLOW)
        elif d.vetoes:
            st["vetoed"] += 1
        conf = d.confidence
        if conf is None or not np.isfinite(conf):
            conf = 0.0
        self._hpe_hist[list(self._hpe_hist)[min(max(int(conf * 5), 0), 4)]] += 1
        for m, v in d.votes.items():
            slot = self._hpe_matrix.setdefault(
                m, {"LONG": 0, "SHORT": 0, "abstain": 0})
            slot["LONG" if v["dir"] > 0 else
                 "SHORT" if v["dir"] < 0 else "abstain"] += 1
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "ARMED" if self._hpe_armed else "SHADOW",
            "strategy": "HPE",
            "last_decision": d.to_dict(),
            "last_signal": ({"ts": sig.ts.isoformat(), "side": sig.side,
                             "entry": sig.entry, "sl": sig.sl, "tp": sig.tp}
                            if sig is not None else None),
            "psychology": d.psychology or {},
            "confidence_histogram": self._hpe_hist,
            "agreement_matrix": self._hpe_matrix,
            "shadow_stats": st,
            "regime": regime.to_dict() if regime is not None else {},
        }
        try:
            path = ROOT / "data" / "hpe_state.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            log.warning("hpe_state write failed: %s", e)

    # ------------------------------------------------------------ positions
    def _manage_positions(self, h1) -> None:
        if h1 is None or len(h1) < 2:
            return
        atr_v = float(atr_fn(h1, self.cfg.atr_period).iloc[-1])
        for pos in self.broker.open_positions():
            if getattr(pos, "meta", None) and pos.meta.get("closed"):
                continue  # P0-A: already closed - never double-manage
            bar_high = float(h1["high"].iloc[-1])
            bar_low = float(h1["low"].iloc[-1])
            action, price = self.strategy.manage_exit(
                {
                    "side": pos.side, "entry": pos.entry, "sl": pos.sl, "tp": pos.tp,
                    "be_moved": pos.be_moved, "trail_active": pos.trail_active,
                    "orig_sl": pos.orig_sl or pos.sl,
                },
                bar_high, bar_low, atr_v,
            )
            if action in ("BE", "TRAIL") and price:
                if self.broker.modify_sl(pos.ticket, price):
                    if action == "BE":
                        pos.be_moved = True
                        log.info("position %s -> breakeven %.2f", pos.ticket, price)
                        audit.log_event("lifecycle", {
                            "event": "breakeven", "ticket": pos.ticket,
                            "sl": price})
                    else:
                        pos.trail_active = True
                        pos.sl = price
                        log.info("position %s trailed sl -> %.2f", pos.ticket, price)
            elif action in ("EXIT_SL", "EXIT_TP") and price:
                # P0-A: the frozen strategy demanded an exit - EXECUTE it.
                reason = "SL" if action == "EXIT_SL" else "TP"
                log.info("position %s exit %s @ %.2f", pos.ticket, reason, price)
                audit.log_event("lifecycle", {
                    "event": "exit", "ticket": pos.ticket, "reason": reason,
                    "price": price})
                self.broker.close_position(pos.ticket, reason=reason,
                                           intended_price=price)
                if any(p.ticket == pos.ticket
                       for p in self.broker.open_positions()):
                    # close FAILED (rejected / unreachable venue) - the audit
                    # trail must never claim a position closed when it did
                    # not; the next tick retries the exit naturally
                    log.error("position %s exit %s FAILED - still open, "
                              "will retry next tick", pos.ticket, reason)
                    audit.log_event("lifecycle", {
                        "event": "exit_failed", "ticket": pos.ticket,
                        "reason": reason})

        # realized fills: one canonical fill event -> account state -> both
        # risk layers (P0-C: the legacy manager's day/streak gates work again)
        try:
            bal = self.broker.balance()
            st = self.account
            if bal is None or bal <= 0:
                # live adapters return None when the venue is unreachable;
                # treating 0/None as a balance would book a fake total loss
                log.warning("fill check skipped: broker balance unavailable")
            elif abs(bal - st.balance) > 0.01 and st.balance > 0:
                pnl = bal - st.balance
                if abs(pnl) > 0.25 * st.balance:
                    # sanity clamp: with <=1 position at ~1.5% risk and <=2.5R
                    # TP no legitimate fill moves the balance by >25%/tick;
                    # a bigger delta means a broken feed, never money truth
                    log.error("implausible balance delta %.2f (%.1f%% of "
                              "balance) - NOT booked as a fill; inspect the "
                              "broker feed", pnl, 100 * pnl / st.balance)
                    audit.log_event("fill", {"rejected_implausible":
                                             round(pnl, 2)})
                else:
                    st.register_fill(pnl, pnl > 0)
                    st.save()
                    session_name = session_of(now_utc())
                    self.risk.register_fill(session_name, pnl, pnl > 0)
                    self.apex.register_fill(pnl, pnl > 0)
                    audit.log_event("fill", {"pnl": round(pnl, 2),
                                             "block_pnl": round(
                                                 self.apex.state.current_block.pnl, 2)})
                    if self.apex.state.current_block.done:
                        cprint(f"[💰] BLOCK TARGET BANKED: "
                               f"{self.apex.state.current_block.pnl:+.2f} USD", GREEN)
                        notify("TARGET HIT",
                               f"4h block banked {self.apex.state.current_block.pnl:+.2f} USD")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ maintenance
    def _maintenance(self) -> None:
        """Hourly 24/7 guards: clock drift, memory, disk, update check.
        Every sub-guard is best-effort; none may ever kill the tick."""
        now = time.time()
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now

        # 1. clock drift vs NTP (alert only, never acts)
        try:
            drift = _ntp_drift_seconds()
            if drift is not None and abs(drift) > 5.0:
                log.warning("clock drift %.1fs vs NTP - sync your OS clock", drift)
                notify("CLOCK DRIFT",
                       f"system clock drifts {drift:+.1f}s vs NTP. "
                       "Sync time (w32tm /resync or timedatectl) - candles misalign.")
        except Exception:  # noqa: BLE001
            pass

        # 2. memory guard: > 500 MB -> clear internal caches
        try:
            rss_mb = _rss_mb()
            if rss_mb and rss_mb > 500:
                log.warning("memory guard: RSS %.0f MB > 500 MB - clearing caches",
                            rss_mb)
                import gc
                gc.collect()
                if self._feed:
                    self._feed._row = None
                    self._feed._row_ts = None
                if isinstance(self.broker, PaperBroker):
                    self.broker._cache = None
        except Exception:  # noqa: BLE001
            pass

        # 3. disk guard: < 1 GB free -> alert
        try:
            import shutil
            free_gb = shutil.disk_usage(ROOT).free / 1e9
            if free_gb < 1.0:
                log.warning("disk guard: %.2f GB free < 1 GB - clean logs", free_gb)
                notify("DISK LOW",
                       f"{free_gb:.2f} GB free on the reaper host. "
                       "Clean data/*.log backups or the machine may wedge.")
        except Exception:  # noqa: BLE001
            pass

        # 4. auto-update check (daily, notify only - never auto-applies)
        if now - self._last_update_check > 86400:
            self._last_update_check = now
            try:
                latest = _latest_release_tag()
                if latest and latest.lstrip("v") != VERSION.lstrip("v"):
                    log.info("update available: %s (local %s)", latest, VERSION)
                    notify("UPDATE AVAILABLE",
                           f"gold-reaper {latest} released (you run {VERSION}). "
                           "Review the changelog, then update manually.")
            except Exception:  # noqa: BLE001
                pass

    def _heartbeat(self, msg: str) -> None:
        now = time.time()
        if now - self._last_heartbeat >= self.cfg.heartbeat_minutes * 60:
            self._last_heartbeat = now
            cprint(f"[♥] {human_local()} | block {self.apex.snapshot()['block_pnl']:+.2f}/"
                   f"{self.apex.snapshot()['block_target']} | {msg}", GRAY)
            log.info("heartbeat: %s", msg)


def sess_str(ts) -> str:
    return session_of(ts)


# ══════════════════════════════════════════════ 24/7 maintenance helpers


def _ntp_drift_seconds(server: str = "pool.ntp.org") -> float | None:
    """SNTP drift estimate, 5s timeout. None on any failure."""
    import socket
    import struct
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(5.0)
            pkt = b"\x1b" + 47 * b"\0"
            t0 = time.time()
            s.sendto(pkt, (server, 123))
            data, _ = s.recvfrom(512)
            t3 = time.time()
        if len(data) < 48:
            return None
        secs = struct.unpack("!I", data[40:44])[0]
        frac = struct.unpack("!I", data[44:48])[0]
        ntp_ts = secs + frac / 2**32 - 2208988800  # NTP epoch -> unix
        rtt = t3 - t0
        return (t0 + t3) / 2 - ntp_ts - rtt / 2
    except Exception:  # noqa: BLE001
        return None


def _rss_mb() -> float | None:
    """Resident memory MB: Linux via /proc, Windows via ctypes, else None."""
    try:
        if os.name == "posix":
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024
            return None
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            h = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                    h, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / (1024 * 1024)
        return None
    except Exception:  # noqa: BLE001
        return None


def _latest_release_tag() -> str | None:
    """Latest GitHub release tag, 10s timeout. None on any failure."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers={"User-Agent": "gold-reaper",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return str(data.get("tag_name") or "") or None
    except Exception:  # noqa: BLE001
        return None


# ═════════════════════════════════════════════════════════════════ multi-account


def _parse_accounts(spec: str) -> list[dict]:
    out = []
    for chunk in spec.split(";"):
        parts = chunk.strip().split("|")
        if not parts or not parts[0]:
            continue
        kind = parts[0].upper()
        acc: dict[str, object] = {"kind": kind, "label": kind.lower()}
        if kind == "MT5" and len(parts) >= 4:
            acc.update({"login": int(parts[1]), "password": parts[2],
                        "server": parts[3]})
        elif kind == "BITGET" and len(parts) >= 4:
            acc.update({"key": parts[1], "secret": parts[2],
                        "passphrase": parts[3]})
        if len(parts) >= 5 and parts[4]:
            acc["label"] = parts[4]
        out.append(acc)
    return out


def _run_account(acc: dict, args) -> int:
    import copy
    cfg = copy.copy(CONFIG)
    cfg.broker = acc["kind"]
    cfg.paper = acc["kind"] == "PAPER" or args.paper
    if acc["kind"] == "MT5":
        cfg.mt5_login = acc.get("login", 0)
        cfg.mt5_password = acc.get("password", "")
        cfg.mt5_server = acc.get("server", cfg.mt5_server)
    elif acc["kind"] == "BITGET":
        cfg.bitget_key = acc.get("key", "")
        cfg.bitget_secret = acc.get("secret", "")
        cfg.bitget_passphrase = acc.get("passphrase", "")
    bot = ReaperApexBot(cfg, account_label=acc["label"])
    return bot.start()


def main() -> int:
    ap = argparse.ArgumentParser(description="GOLD REAPER APEX :: XAUUSD hunter")
    ap.add_argument("--broker", choices=["MT5", "BITGET", "PAPER"], default=None)
    ap.add_argument("--paper", action="store_true", help="force paper mode")
    ap.add_argument("--dashboard", action="store_true",
                    help="spawn live console on :8050")
    ap.add_argument("--reset-breakers", action="store_true",
                    help="clear latched circuit breakers")
    args = ap.parse_args()

    # P1-C: strict validation BEFORE anything runs - bad config never starts
    try:
        validated = validate_config()
        log.info("config validated: %s", validated)
    except ConfigError as e:
        cprint(f"[!] CONFIG INVALID - refusing to start: {e}", RED)
        return 2

    # SD-10/SD-12 mitigation: corrupt risk-truth files refuse to start
    # (the frozen modules themselves fail open - we fail closed here)
    try:
        _guard_runtime_state()
    except AccountStateError as e:
        cprint(f"[!] RISK STATE REFUSED: {e}", RED)
        return 3

    if args.reset_breakers:
        ApexRisk.reset_breakers()
        return 0

    if args.dashboard:
        t = threading.Thread(target=lambda: _spawn_dashboard(), daemon=True)
        t.start()

    accounts = _parse_accounts(__import__("os").getenv("APEX_ACCOUNTS", "")
                               ) if __import__("os").getenv("APEX_ACCOUNTS") else []
    if len(accounts) > 1:
        cprint(f"[*] multi-account mode: {len(accounts)} hunters", YELLOW)
        threads = []
        results = {}
        for acc in accounts:
            th = threading.Thread(target=lambda a=acc: results.update(
                {a["label"]: _run_account(a, args)}), daemon=False)
            threads.append(th)
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return 0

    if args.broker:
        CONFIG.broker = args.broker
        CONFIG.paper = args.broker == "PAPER"
    if args.paper:
        CONFIG.paper = True
        if not args.broker:
            CONFIG.broker = "PAPER"
    if accounts:
        return _run_account(accounts[0], args)

    # supervisor: the bot NEVER exits unexpectedly. Fatal loop errors log,
    # sleep 30s, rebuild and keep hunting. SIGINT (Ctrl+C) is respected.
    supervisor = os.getenv("SUPERVISOR", "1").strip().lower() not in ("0", "false", "no")
    while True:
        try:
            code = ReaperApexBot(CONFIG).start()
        except AccountStateError as e:
            cprint(f"[!] ACCOUNT STATE REFUSED: {e}", RED)
            return 3
        if code == 0:                      # clean shutdown or Ctrl+C
            return 0
        if code == 3:                      # P1-B halt: manual fix required
            log.error("broker halt (exit 3) - supervisor will NOT restart; "
                      "fix .env and start again")
            return 3
        if not supervisor:
            return code
        log.error("supervisor: bot exited code=%s - restarting in 30s", code)
        try:
            notify("SUPERVISOR", "bot crashed - restarting in 30s (watchdog + "
                   "service restarts stay armed)")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30)


def _spawn_dashboard() -> None:
    try:
        import uvicorn

        from core.config_validation import resolve_dashboard_bind
        from core.dashboard import app
        host, port = resolve_dashboard_bind(8050)
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard failed: %s", e)


if __name__ == "__main__":
    from core.logger import force_utf8_streams
    force_utf8_streams()
    sys.exit(main())
