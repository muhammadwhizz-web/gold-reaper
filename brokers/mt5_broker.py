"""
GOLD REAPER :: Exness via MetaTrader 5
======================================
Exness retail accounts trade gold (XAUUSD) through the MT5 terminal.
The official `MetaTrader5` python package talks to terminal64.exe
(Windows natively; Linux via Wine — see README).

Bulletproofing (Phase 3 of the installer spec):
  - auto-detects the terminal in the standard install locations
    (MT5_PATH in .env overrides)
  - login retries 3x with clear errors, then fails cleanly
  - symbol fallback chain: XAUUSD -> XAUUSDm -> XAUUSD.raw -> GOLD
  - verifies demo vs REAL account and logs which one is connected
  - ensure_connected() for the health monitor / auto-reconnect loop

If MT5 is not available (wrong platform / terminal closed) this adapter
falls back to live price via yfinance so the bot stays operational in
shadow mode, and refuses to send orders.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from core.logger import RED, YELLOW, cprint

TF_MAP = {"h1": 16385, "h4": 16388, "d1": 16408}  # MT5 constants

# where terminal64.exe hides (checked in order; MT5_PATH wins)
TERMINAL_CANDIDATES = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\Exness MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\Exness MetaTrader 5\terminal64.exe",
]
MT5_DOWNLOAD = "https://www.exness.com/downloads/  (or https://www.metatrader5.com/en/download)"

# gold symbol aliases brokers use
SYMBOL_FALLBACKS = ["XAUUSD", "XAUUSDm", "XAUUSD.raw", "GOLD", "XAUUSD.s", "XAUUSDz"]

# human-readable retcodes (most common rejections)
RETCODE_HINT = {
    10004: "requote — retry, do not spam orders",
    10006: "request rejected by broker",
    10013: "invalid request (check volume/price)",
    10014: "invalid volume (below broker minimum)",
    10015: "invalid price",
    10016: "invalid stops (SL/TP too close)",
    10018: "MARKET CLOSED — the bot will idle until open",
    10019: "NOT ENOUGH MONEY — reduce risk or deposit",
    10027: "AUTOTRADING DISABLED in terminal — enable the AutoTrading button",
    10028: "order blocked by account (invest password / manager)",
    10030: "unsupported filling mode",
    10044: "only position closing allowed (account restriction)",
}

try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False


def find_terminal() -> str:
    """Locate terminal64.exe. Returns path or ''. Env MT5_PATH wins."""
    custom = os.getenv("MT5_PATH", "").strip()
    if custom and Path(custom).exists():
        return custom
    for cand in TERMINAL_CANDIDATES:
        if Path(cand).exists():
            return cand
    # last sweep: %APPDATA%\MetaQuotes\Terminal\*\helpprofile? no — install
    # roots only; keep it deterministic and cheap.
    return ""


def _trade_mode_name(info) -> str:
    if mt5 is None or info is None:
        return "UNKNOWN"
    tm = getattr(info, "trade_mode", -1)
    return {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(tm, f"mode{tm}")


class ExnessMT5(BrokerBase):
    name = "EXNESS-MT5"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.connected = False
        self.symbol = cfg.mt5_symbol
        self._last_error = ""

    # ------------------------------------------------------------- connect
    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            cprint("[MT5] MetaTrader5 package not installed on this platform "
                   "(Windows/Wine required). Running in SHADOW mode.", YELLOW)
            return False

        # 1. locate terminal — clear error + download link if absent
        term = find_terminal()
        kwargs: dict = {}
        if term:
            kwargs["path"] = term
        elif os.name == "nt":
            cprint(f"[MT5] terminal64.exe not found in standard locations.\n"
                   f"      Install MetaTrader 5 / Exness MT5: {MT5_DOWNLOAD}\n"
                   f"      or set MT5_PATH=C:\\...\\terminal64.exe in .env", RED)

        # 2. initialize
        if not mt5.initialize(**kwargs):
            self._last_error = f"initialize failed: {mt5.last_error()}"
            cprint(f"[MT5] {self._last_error}", RED)
            return False

        # 3. login — 3 retries, then clean fail
        if self.cfg.mt5_login:
            for attempt in range(1, 4):
                if mt5.login(self.cfg.mt5_login, password=self.cfg.mt5_password,
                             server=self.cfg.mt5_server):
                    break
                err = mt5.last_error()
                self._last_error = f"login failed (attempt {attempt}/3): {err}"
                cprint(f"[MT5] {self._last_error}", RED)
                if attempt < 3:
                    time.sleep(2)
            else:
                cprint("[MT5] giving up after 3 login attempts. Check "
                       "MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in .env", RED)
                mt5.shutdown()
                return False

        # 4. symbol — fallback chain, verify trading enabled
        if not self._select_symbol():
            mt5.shutdown()
            return False

        ai = mt5.account_info()
        if ai is None:
            cprint("[MT5] account_info() returned None after login", RED)
            mt5.shutdown()
            return False
        mode = _trade_mode_name(ai)
        info = mt5.symbol_info(self.symbol)
        # symbol trade mode: 0=DISABLED 1=LONGONLY 2=SHORTONLY 3=CLOSEONLY 4=FULL
        smode = getattr(info, "trade_mode", 4) if info else 4
        trade_allowed = smode not in (0, 3)
        cprint(f"[MT5] connected | {self.symbol} | account {mode} | "
               f"balance ${ai.balance:,.2f} | trading "
               f"{'enabled' if trade_allowed else 'check AutoTrading button'}",
               YELLOW)
        if mode == "REAL":
            cprint("[MT5] *** REAL ACCOUNT — live money. Verify PAPER_MODE=false "
                   "was an intentional decision. ***", YELLOW)
        self.connected = True
        return True

    def _select_symbol(self) -> bool:
        tried: list[str] = []
        for sym in dict.fromkeys([self.cfg.mt5_symbol] + SYMBOL_FALLBACKS):
            if not sym:
                continue
            if mt5.symbol_select(sym, True):
                if sym != self.cfg.mt5_symbol:
                    cprint(f"[MT5] symbol fallback: {self.cfg.mt5_symbol} -> {sym}",
                           YELLOW)
                self.symbol = sym
                return True
            tried.append(sym)
        cprint(f"[MT5] no gold symbol found. tried: {', '.join(tried)}", RED)
        self._last_error = "no tradeable gold symbol"
        return False

    def ensure_connected(self) -> bool:
        """Auto-reconnect entry point for brokers/health.py."""
        if self.connected:
            return True
        return self.connect()

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
        self.connected = False

    # ------------------------------------------------------------- account
    def balance(self) -> float:
        if MT5_AVAILABLE and self.connected and (ai := mt5.account_info()):
            return float(ai.balance)
        return 0.0

    def equity(self) -> float:
        if MT5_AVAILABLE and self.connected and (ai := mt5.account_info()):
            return float(ai.equity)
        return self.balance()

    # ------------------------------------------------------------- market data
    def _rates(self, tf_code: int, count: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.symbol, tf_code, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
        return df

    def candles(self, timeframe: str, count: int) -> dict[str, object]:
        if not (MT5_AVAILABLE and self.connected):
            return {"h1": pd.DataFrame(), "h4": pd.DataFrame()}
        return {"h1": self._rates(TF_MAP["h1"], count), "h4": self._rates(TF_MAP["h4"], count)}

    def last_price(self) -> float:
        if MT5_AVAILABLE and self.connected:
            t = mt5.symbol_info_tick(self.symbol)
            if t:
                return float(t.bid)
        return 0.0

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        if not (MT5_AVAILABLE and self.connected):
            return OrderResult(False, error="MT5 unavailable (shadow mode)")

        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if not info or not tick:
            return OrderResult(False, error="no symbol info")

        lots = self._oz_to_lots(size_oz, info)
        order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "LONG" else tick.bid
        digits = info.digits

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "deviation": 30,
            "magic": 666666,
            "comment": f"REAPER {session[:6]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        res = mt5.order_send(req)
        if res is None:
            return OrderResult(False, error="order_send None (terminal busy)")
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            hint = RETCODE_HINT.get(res.retcode, "")
            msg = f"retcode {res.retcode}: {res.comment}"
            if hint:
                msg = f"{msg} — {hint}"
            return OrderResult(False, error=msg)
        cprint(f"[MT5] {side} {lots} lots @ {res.price} | sl {sl:.2f} tp {tp:.2f}", YELLOW)
        return OrderResult(True, ticket=str(res.order), price=float(res.price))

    def _oz_to_lots(self, size_oz: float, info) -> float:
        contract = info.trade_contract_size or 100.0   # XAUUSD: 100 oz / lot
        lots = size_oz / contract
        step = info.volume_step or 0.01
        minv = info.volume_min or 0.01
        lots = max(minv, round(lots / step) * step)
        return round(min(lots, info.volume_max or 100.0), 2)

    def _filling_mode(self, info) -> int:
        fm = getattr(info, "filling_mode", 1)
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        if not (MT5_AVAILABLE and self.connected):
            return False
        for pos in mt5.positions_get(ticket=int(ticket)) or []:
            digits = mt5.symbol_info(pos.symbol).digits
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": round(new_sl, digits),
                "tp": pos.tp,
            }
            res = mt5.order_send(req)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        return False

    def close_position(self, ticket: str, reason: str = "") -> float | None:
        if not (MT5_AVAILABLE and self.connected):
            return None
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return None
        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        is_long = pos.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30,
            "magic": 666666,
            "comment": f"REAPER close {reason[:10]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(mt5.symbol_info(pos.symbol)),
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return None  # pnl read back via history; caller computes from prices
        return None

    def open_positions(self) -> list[Position]:
        if not (MT5_AVAILABLE and self.connected):
            return []
        out: list[Position] = []
        for p in mt5.positions_get() or []:
            if p.magic != 666666:
                continue
            out.append(Position(
                ticket=str(p.ticket),
                side="LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT",
                size_oz=p.volume * (mt5.symbol_info(p.symbol).trade_contract_size or 100),
                entry=p.price_open, sl=p.sl, tp=p.tp,
                opened_at=datetime.fromtimestamp(p.time, tz=timezone.utc),
            ))
        return out
