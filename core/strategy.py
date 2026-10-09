"""
GOLD REAPER :: REAPER-X Strategy Engine
=======================================
Designed from 20-year XAU/USD recon:
  - Gold trends hard, then pulls back to the EMA20 area before continuing.
  - The London / New-York overlap (12:00-16:00 UTC) carries the largest
    hourly ranges (recon: 0.60% avg vs 0.33% late US) -> hunt there.

Logic (H1 execution, H4 bias):
  1. BIAS   : H4 close vs EMA50/EMA200 defines LONG / SHORT / NEUTRAL.
  2. RESET  : price must pull back into the EMA20 band and RSI must reset
              into the zone (longs 38-52, shorts 48-62).
  3. TRIGGER: a candle closing back in the bias direction + RSI turning.
  4. RISK   : SL = 1.5 x ATR, TP = 2.6 x ATR, BE at +1R, trail at +1.5R.

Signal object is broker-agnostic -> same code drives MT5, Bitget, paper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from core.config import CONFIG, Config
from core.indicators import adx, atr, bearish_body, bullish_body, ema, rsi
from core.sessions import in_blackout, session_of


@dataclass
class Signal:
    side: str                    # "LONG" | "SHORT"
    entry: float
    sl: float
    tp: float
    atr: float
    reason: str
    session: str
    ts: datetime
    rsi: float = 0.0
    adx: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.sl)


@dataclass
class StrategyState:
    bias: str = "NEUTRAL"
    htf_trend_strength: float = 0.0
    last_signal_ts: pd.Timestamp | None = None


class ReaperX:
    """The hunter. Feed it dataframes, it returns kills (or nothing)."""

    name = "REAPER-X"

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or CONFIG
        self.state = StrategyState()

    # ------------------------------------------------------------------ bias
    def compute_bias(self, h4: pd.DataFrame) -> str:
        c = self.cfg
        fast = ema(h4["close"], min(c.htf_trend_ema_fast, max(2, len(h4) // 4)))
        slow = ema(h4["close"], min(c.htf_trend_ema_slow, max(3, len(h4) // 2)))
        close = float(h4["close"].iloc[-1])
        f, s = float(fast.iloc[-1]), float(slow.iloc[-1])
        self.state.htf_trend_strength = abs(f - s) / max(close, 1e-9) * 100

        if close > f > s:
            self.state.bias = "LONG"
        elif close < f < s:
            self.state.bias = "SHORT"
        else:
            self.state.bias = "NEUTRAL"
        return self.state.bias

    # -------------------------------------------------------------- signals
    def evaluate(self, h1: pd.DataFrame, h4: pd.DataFrame,
                 ts: datetime) -> Signal | None:
        """Run the full pipeline. Returns a Signal or None."""
        c = self.cfg
        if len(h1) < max(c.htf_trend_ema_fast, c.ema_pullback, c.rsi_period) + 5:
            return None
        if len(h4) < 30:
            return None

        bias = self.compute_bias(h4)
        if bias == "NEUTRAL":
            return None

        # session gate
        sess = session_of(ts)
        window = c.session_windows.get(sess)
        if window is None:
            return None
        # v2.3 hardened hunt window (walk-forward: hours 14-15 UTC bleed)
        if c.entry_hours_utc and ts.hour not in c.entry_hours_utc:
            return None

        # US-data blackout windows
        if in_blackout(ts, c.blackout_hours_utc):
            return None

        close = h1["close"]
        e20 = ema(close, c.ema_pullback)
        r = rsi(close, c.rsi_period)
        a = atr(h1, c.atr_period)
        dx = adx(h1, c.atr_period)

        i = -1  # last closed bar
        px = float(close.iloc[i])
        e = float(e20.iloc[i])
        rsi_v = float(r.iloc[i])
        rsi_prev = float(r.iloc[i - 1])
        atr_v = float(a.iloc[i])
        adx_v = float(dx.iloc[i])

        if atr_v < c.min_atr_price:
            return None  # dead tape
        if adx_v < c.min_adx:
            return None  # chop — reaper only hunts trends

        # pullback proximity: price within 0.6 x ATR of EMA20
        near_ema = abs(px - e) <= 0.6 * atr_v

        sig: Signal | None = None
        if bias == "LONG" and near_ema and c.rsi_long_zone[0] <= rsi_v <= c.rsi_long_zone[1]:
            # reset done; need bullish trigger candle with RSI turning up
            if bullish_body(h1.iloc[i]) and rsi_v >= rsi_prev:
                entry = px
                sl = entry - c.sl_atr_mult * atr_v
                tp = entry + c.tp_r * c.sl_atr_mult * atr_v
                sig = Signal("LONG", entry, sl, tp, atr_v,
                             f"bias-up pullback EMA20 rsi {rsi_v:.1f} adx {adx_v:.0f}",
                             sess, ts, rsi_v, adx_v)

        elif bias == "SHORT" and near_ema and c.rsi_short_zone[0] <= rsi_v <= c.rsi_short_zone[1]:
            if bearish_body(h1.iloc[i]) and rsi_v <= rsi_prev:
                entry = px
                sl = entry + c.sl_atr_mult * atr_v
                tp = entry - c.tp_r * c.sl_atr_mult * atr_v
                sig = Signal("SHORT", entry, sl, tp, atr_v,
                             f"bias-dn pullback EMA20 rsi {rsi_v:.1f} adx {adx_v:.0f}",
                             sess, ts, rsi_v, adx_v)

        if sig is not None:
            sig.meta = {"ema20": e, "bias_strength": self.state.htf_trend_strength}
            self.state.last_signal_ts = h1.index[i]
        return sig

    # ------------------------------------------------------- open trade mng
    def manage_exit(self, pos: dict, bar_high: float, bar_low: float,
                    atr_v: float) -> tuple[str, float | None]:
        """
        Position manager called every bar for an open trade.
        pos: {side, entry, sl, tp, be_moved, trail_active}
        Returns (action, price) where action in {HOLD, BE, TRAIL, EXIT_SL, EXIT_TP}
        """
        c = self.cfg
        entry, sl, tp = pos["entry"], pos["sl"], pos["tp"]
        side = pos["side"]
        r_dist = abs(entry - sl if not pos.get("be_moved") else pos["orig_sl"])

        # initial SL/TP hits (conservative: check both)
        if side == "LONG":
            if bar_low <= sl:
                return "EXIT_SL", sl
            if bar_high >= tp:
                return "EXIT_TP", tp
            move = bar_high - entry
        else:
            if bar_high >= sl:
                return "EXIT_SL", sl
            if bar_low <= tp:
                return "EXIT_TP", tp
            move = entry - bar_low

        r_mult = move / r_dist if r_dist > 0 else 0.0

        if not pos.get("be_moved") and r_mult >= c.breakeven_at_r:
            return "BE", entry + (0.05 * atr_v if side == "LONG" else -0.05 * atr_v)

        if pos.get("be_moved") and not pos.get("trail_active") and r_mult >= c.trail_start_r:
            return "TRAIL", (bar_high - c.trail_atr_mult * atr_v if side == "LONG"
                             else bar_low + c.trail_atr_mult * atr_v)

        if pos.get("trail_active"):
            trail = (bar_high - c.trail_atr_mult * atr_v if side == "LONG"
                     else bar_low + c.trail_atr_mult * atr_v)
            better = trail > sl if side == "LONG" else trail < sl
            if better:
                return "TRAIL", trail

        return "HOLD", None
