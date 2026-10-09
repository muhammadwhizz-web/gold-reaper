"""
GOLD REAPER :: Paper Broker
===========================
Full simulation with realistic spread + slippage. Zero risk, full behavior.
Uses Yahoo live hourly data so strategy logic is exercised end-to-end.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from core.logger import GREEN, cprint

SPREAD = 0.35      # gold spread $ (Exness-like)
SLIPPAGE = 0.05


class PaperBroker(BrokerBase):
    name = "PAPER"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.balance_val: float = cfg.starting_balance
        self.positions: dict[str, Position] = {}
        self._cache: tuple[str, dict[str, pd.DataFrame]] | None = None
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------- data
    def _yahoo(self, period: str, interval: str) -> pd.DataFrame:
        import yfinance as yf
        df = yf.download("GC=F", period=period, interval=interval,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "time"
        return df

    def candles(self, timeframe: str, count: int) -> dict[str, pd.DataFrame]:
        import time as _t
        now = _t.time()
        if self._cache and now - self._cache_ts < 120:
            cached = self._cache[1]
            return {"h1": cached["h1"].tail(count), "h4": cached["h4"].tail(count)}
        h1 = self._yahoo("60d", "1h")
        h4 = h1.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna() if not h1.empty else pd.DataFrame()
        self._cache = (str(now), {"h1": h1, "h4": h4})
        self._cache_ts = now
        return {"h1": h1.tail(count), "h4": h4.tail(count)}

    def last_price(self) -> float:
        h1 = self.candles("h1", 2)["h1"]
        return float(h1["close"].iloc[-1]) if not h1.empty else 0.0

    # ------------------------------------------------------------- account
    def connect(self) -> bool:
        cprint(f"[PAPER] armed | virtual balance ${self.balance_val:,.2f} | "
               f"spread ${SPREAD} | slippage ${SLIPPAGE}", GREEN)
        return True

    def disconnect(self) -> None:
        pass

    def balance(self) -> float:
        return self.balance_val

    def equity(self) -> float:
        eq = self.balance_val
        px = self.last_price()
        for p in self.positions.values():
            if px:
                eq += self._unrealized(p, px)
        return eq

    def _unrealized(self, p: Position, px: float) -> float:
        diff = (px - p.entry) if p.side == "LONG" else (p.entry - px)
        return diff * p.size_oz

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        px = self.last_price()
        if not px:
            return OrderResult(False, error="no price feed")
        fill = px + (SPREAD + SLIPPAGE) if side == "LONG" else px - (SPREAD + SLIPPAGE)
        ticket = uuid.uuid4().hex[:10]
        self.positions[ticket] = Position(
            ticket=ticket, side=side, size_oz=size_oz, entry=fill, sl=sl, tp=tp,
            opened_at=datetime.now(timezone.utc), orig_sl=sl, session=session,
            reason=reason,
        )
        cprint(f"[PAPER] {side} {size_oz:.3f} oz @ {fill:.2f} | sl {sl:.2f} tp {tp:.2f}",
               GREEN)
        return OrderResult(True, ticket=ticket, price=fill)

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        if ticket in self.positions:
            self.positions[ticket].sl = new_sl
            return True
        return False

    def close_position(self, ticket: str, reason: str = "") -> float | None:
        p = self.positions.pop(ticket, None)
        if not p:
            return None
        px = self.last_price()
        exit_px = px - SLIPPAGE if p.side == "LONG" else px + SLIPPAGE
        pnl = self._unrealized(p, exit_px)
        self.balance_val += pnl
        cprint(f"[PAPER] closed {p.side} {ticket} @ {exit_px:.2f} pnl {pnl:+.2f} "
               f"| balance ${self.balance_val:,.2f}", GREEN)
        return pnl

    def open_positions(self) -> list[Position]:
        return list(self.positions.values())
