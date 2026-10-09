#!/usr/bin/env python3
"""
██╗  ██╗ █████╗ ██╗     ██╗
██║  ██║██╔══██╗██║     ██║
███████║███████║██║     ██║
██╔══██║██╔══██║██║     ██║
██║  ██║██║  ██║███████╗███████╗
╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝
GOLD REAPER :: 24/7 XAU/USD autonomous hunter
Targets: $20 / session · $120 / day — or die trying (with circuit breakers).

Run:
  python bot.py                 # uses .env (default PAPER mode)
  python bot.py --broker MT5    # force Exness MT5
  python bot.py --broker BITGET
  python bot.py --broker PAPER
"""
from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from brokers.base import BrokerBase, Position  # noqa: E402
from brokers.bitget_broker import BitgetBroker  # noqa: E402
from brokers.mt5_broker import ExnessMT5  # noqa: E402
from brokers.paper_broker import PaperBroker  # noqa: E402
from core.config import CONFIG, Config  # noqa: E402
from core.logger import GREEN, GRAY, RED, YELLOW, banner, cprint, setup_logger  # noqa: E402
from core.risk import RiskManager  # noqa: E402
from core.sessions import (  # noqa: E402
    friday_cutoff_reached,
    human_local,
    is_market_open,
    minutes_until_market_close,
    now_utc,
    session_of,
)
from core.strategy import ReaperX, Signal  # noqa: E402

log = setup_logger(CONFIG.log_file)


class GoldReaperBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.strategy = ReaperX(cfg)
        self.risk = RiskManager(cfg)
        self.broker: BrokerBase = PaperBroker(cfg)
        self.running = True
        self._last_heartbeat = 0.0
        self._last_bar_ts = None

    # ------------------------------------------------------------ lifecycle
    def build_broker(self, choice: str) -> BrokerBase:
        if choice == "MT5":
            return ExnessMT5(self.cfg)
        if choice == "BITGET":
            return BitgetBroker(self.cfg)
        return PaperBroker(self.cfg)

    def start(self) -> int:
        cprint(banner(), RED)
        log.info("GOLD REAPER starting | broker=%s paper=%s",
                 self.cfg.broker, self.cfg.paper)
        cprint(f"[*] broker          : {self.cfg.broker}", YELLOW)
        cprint(f"[*] mission         : ${self.cfg.target_per_session:.0f}/session "
               f"· ${self.cfg.target_per_day:.0f}/day", YELLOW)
        cprint(f"[*] risk/trade      : {self.cfg.risk_per_trade_pct}% | "
               f"daily loss stop: {self.cfg.max_daily_loss_pct}%", YELLOW)
        cprint(f"[*] local time      : {human_local(tz_name='Asia/Karachi')}", YELLOW)

        self.risk.load()
        self.broker = self.build_broker(self.cfg.broker)
        if not self.broker.connect():
            cprint("[!] primary broker unavailable -> falling back to PAPER mode", RED)
            self.broker = PaperBroker(self.cfg)
            self.broker.connect()
            if self.cfg.broker != "PAPER":
                log.warning("fallback to paper mode")

        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        cprint("[✓] REAPER ONLINE. hunting...", GREEN)
        exit_code = 0
        try:
            while self.running:
                self.tick()
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("fatal loop error: %s", e)
            exit_code = 1
        finally:
            self.shutdown()
        return exit_code

    def _stop(self, *_args) -> None:
        cprint("\n[!] shutdown signal received. reaping quietly...", YELLOW)
        self.running = False

    def shutdown(self) -> None:
        try:
            pos = self.broker.open_positions()
            for p in pos:
                log.info("open position on exit: %s %s %.2f oz @ %.2f",
                         p.ticket, p.side, p.size_oz, p.entry)
            self.risk.save()
            self.broker.disconnect()
            cprint(f"[*] day pnl: {self.risk.state.day.realized_pnl:+.2f} USD | "
                   f"balance {self.risk.state.balance:,.2f}", GREEN)
            cprint("[✓] REAPER offline. state saved.", GRAY)
        except Exception:  # noqa: BLE001
            log.exception("shutdown error")

    # ------------------------------------------------------------ main tick
    def tick(self) -> None:
        self.risk.rollover_day()
        ts = now_utc()

        if not is_market_open(ts):
            self._heartbeat("market closed (weekend)")
            return

        # 1. market data
        data = self.broker.candles("h1", 400)
        h1: pd = data["h1"]
        h4: pd = data["h4"]
        if h1 is None or len(h1) < 60 or h4 is None or len(h4) < 30:
            self._heartbeat("warming up: not enough candles")
            return
        # drop the still-forming bar
        h1_closed = h1.iloc[:-1] if h1.index[-1] > ts - __import__("pandas").Timedelta("1h") else h1
        last_bar = h1_closed.index[-1]
        if self._last_bar_ts == last_bar:
            self._manage_positions(h1)          # intrabar management on same bar
            return
        self._last_bar_ts = last_bar

        # 2. equity sync
        eq = None
        try:
            eq = self.broker.equity()
            if eq > 0:
                self.risk.update_equity(eq)
            else:
                eq = None
        except Exception:  # noqa: BLE001
            pass

        # 3. manage exits first (BE / trail / manual SL-TP checks)
        self._manage_positions(h1_closed)

        # 4. weekend / friday guard
        if friday_cutoff_reached(ts, self.cfg.friday_last_entry_utc):
            self._heartbeat("friday cutoff - no new entries")
            return
        if minutes_until_market_close(ts) < self.cfg.no_entry_minutes_before_weekend_close \
                and ts.weekday() == 4:
            self._heartbeat("pre-weekend-close blackout")
            return

        # 5. risk gates
        sess = session_of(ts)
        allowed, why = self.risk.can_trade(sess)
        if not allowed:
            self._heartbeat(why)
            return

        # 6. strategy signal
        sig = self.strategy.evaluate(h1_closed, h4, ts)
        if sig is None:
            self._heartbeat(f"{sess}: no signal (bias {self.strategy.state.bias})")
            return

        # 7. execute
        self._execute(sig, eq if eq else self.risk.state.equity)

    # ------------------------------------------------------------ execution
    def _execute(self, sig: Signal, equity: float) -> None:
        open_pos = self.broker.open_positions()
        if len(open_pos) >= self.cfg.max_open_trades:
            return
        size = self.risk.position_size(sig.entry, sig.sl, equity)
        if size <= 0:
            log.warning("position size computed 0 - skip")
            return
        res = self.broker.market_order(sig.side, size, sig.sl, sig.tp,
                                       sig.session, sig.reason)
        if res.ok:
            self.risk.session_stats(sig.session)
            self.risk.save()
            log.info("EXECUTED %s %.3f oz @ %.2f sl %.2f tp %.2f | %s | %s",
                     sig.side, size, res.price or sig.entry, sig.sl, sig.tp,
                     sig.session, sig.reason)
            self._journal(sig, size, res.price or sig.entry)
            cprint(f"[>>>] KILL ORDER SENT {sig.side} {size:.3f} oz "
                   f"@ {(res.price or sig.entry):.2f}", YELLOW)

    def _journal(self, sig: Signal, size: float, price: float) -> None:
        f = self.cfg.trades_file
        new = not f.exists()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts_utc", "side", "size_oz", "entry", "sl", "tp",
                            "session", "risk_oz_atr", "reason"])
            w.writerow([datetime.now(timezone.utc).isoformat(), sig.side, size,
                        price, sig.sl, sig.tp, sig.session, sig.atr, sig.reason])

    # ------------------------------------------------------------ positions
    def _manage_positions(self, h1) -> None:
        if h1 is None or len(h1) < 2:
            return
        from core.indicators import atr as atr_fn
        atr_v = float(atr_fn(h1, self.cfg.atr_period).iloc[-1])
        for pos in self.broker.open_positions():
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
                    else:
                        pos.trail_active = True
                        pos.sl = price
                        log.info("position %s trailed sl -> %.2f", pos.ticket, price)

    def _heartbeat(self, msg: str) -> None:
        now = time.time()
        if now - self._last_heartbeat >= self.cfg.heartbeat_minutes * 60:
            self._last_heartbeat = now
            d = self.risk.state.day
            cprint(f"[♥] {human_local()} | day pnl {d.realized_pnl:+.2f} "
                   f"({d.wins}W/{d.losses}L) | {msg}", GRAY)
            log.info("heartbeat: %s | day pnl %.2f", msg, d.realized_pnl)


def main() -> int:
    ap = argparse.ArgumentParser(description="GOLD REAPER :: XAUUSD hunter")
    ap.add_argument("--broker", choices=["MT5", "BITGET", "PAPER"], default=None)
    args = ap.parse_args()
    cfg = CONFIG
    if args.broker:
        cfg.broker = args.broker
        cfg.paper = args.broker == "PAPER"
    bot = GoldReaperBot(cfg)
    return bot.start()


if __name__ == "__main__":
    sys.exit(main())
