"""
GOLD REAPER HPE :: Survival Risk Layer (additive, never replaces)
=================================================================
Phase 6. Sits ON TOP of the existing frozen risk stack (core/risk.py,
core/risk_apex.py - both untouched). The frozen managers keep every
existing control; this layer can only SHRINK or BLOCK, never grow.

    confidence-scaled sizing  0.3% .. 1.5% of equity by ensemble confidence
    regime-scaled sizing      trends 1.0 / range 0.8 / chop 0.6 / crisis 0
    equity-curve trading      paused while equity < its own rolling mean
    Kelly cap                 risk never exceeds 1/4 Kelly of the ledger
    loss-streak brake         2 consecutive losses -> 50% size until a win
    session target            +$20 per 4h block -> done for that block
    daily / weekly / monthly  -2% / -5% / -10% hard stops

Honesty: negative-Kelly ledgers BLOCK trading rather than size down to a
token risk - if the ledger says the edge is gone, stop pulling the lever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

REGIME_SCALE = {"TREND_UP": 1.0, "TREND_DOWN": 1.0, "RANGE": 0.8,
                "VOLATILE_CHOP": 0.6, "CRISIS": 0.0}


@dataclass
class RiskHPEConfig:
    base_risk_pct: float = 1.0
    min_risk_pct: float = 0.3
    max_risk_pct: float = 1.5
    daily_stop_pct: float = 2.0
    weekly_stop_pct: float = 5.0
    monthly_stop_pct: float = 10.0
    session_target_usd: float = 20.0
    kelly_fraction: float = 0.25
    kelly_min_trades: int = 30
    loss_streak_brake: int = 2
    brake_scale: float = 0.5
    equity_ma_window: int = 50


@dataclass
class RiskHPE:
    """Rolling ledger + sizing + entry gates. Self-contained, testable."""

    cfg: RiskHPEConfig = field(default_factory=RiskHPEConfig)
    starting_equity: float = 10_000.0

    def __post_init__(self) -> None:
        self.ledger: list[dict] = []       # {"ts": pd.Timestamp, "pnl": float}
        self._streak_losses: int = 0

    # ------------------------------------------------------------- ledger
    def record_close(self, pnl: float, ts: datetime | pd.Timestamp | None = None) -> None:
        t = pd.Timestamp.now(tz="UTC") if ts is None else pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        self.ledger.append({"ts": t, "pnl": float(pnl)})
        if pnl < 0:
            self._streak_losses += 1
        else:
            self._streak_losses = 0

    @property
    def loss_streak(self) -> int:
        return self._streak_losses

    def _cum_equity_curve(self) -> pd.Series:
        if not self.ledger:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self.ledger).set_index("ts").sort_index()
        return self.starting_equity + df["pnl"].cumsum()

    # ------------------------------------------------------------- sizing
    def risk_pct(self, confidence: float, regime: str) -> float:
        """Final risk % after confidence, regime, brake and Kelly layers."""
        c = self.cfg
        conf = float(np.clip(confidence, 0.0, 1.0))
        span = c.max_risk_pct - c.min_risk_pct
        ramp = float(np.clip((conf - 0.4) / 0.5, 0.0, 1.0))
        risk = c.min_risk_pct + ramp * span
        risk *= REGIME_SCALE.get(regime, 0.6)
        if self._streak_losses >= c.loss_streak_brake:
            risk *= c.brake_scale
        cap = self.kelly_cap_pct()
        if cap is not None:
            risk = min(risk, cap)
        if REGIME_SCALE.get(regime, 0.6) <= 0.0:
            return 0.0
        return float(round(np.clip(risk, 0.0, c.max_risk_pct), 4))

    def kelly_cap_pct(self) -> float | None:
        """1/4-Kelly cap as a risk %. None while the ledger is too short."""
        c = self.cfg
        if len(self.ledger) < c.kelly_min_trades:
            return None
        pnls = np.array([t["pnl"] for t in self.ledger], dtype=float)
        wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
        if len(wins) == 0 or len(losses) == 0:
            return 0.0                      # one-sided ledger -> no edge claim
        w = len(wins) / len(pnls)
        r = wins.mean() / abs(losses.mean()) if losses.mean() != 0 else 0.0
        f_star = w - (1.0 - w) / r if r > 0 else 0.0
        if f_star <= 0:
            return 0.0                      # negative edge -> stop trading
        return float(100.0 * c.kelly_fraction * f_star)

    # -------------------------------------------------------------- gates
    def _window_pnl(self, ts: pd.Timestamp, freq: str) -> float:
        if not self.ledger:
            return 0.0
        df = pd.DataFrame(self.ledger).set_index("ts").sort_index()
        # work in tz-naive UTC: to_period() silently drops tz and warns
        naive = df.index.tz_convert("UTC").tz_localize(None)
        ts_n = ts.tz_convert("UTC").tz_localize(None)
        key = {"D": naive.floor("D"),
               "W": naive.to_period("W-SUN").to_timestamp(),
               "M": naive.to_period("M").to_timestamp()}[freq]
        g = df.groupby(key)["pnl"].sum()
        bucket = {"D": ts_n.floor("D"),
                  "W": ts_n.to_period("W-SUN").to_timestamp(),
                  "M": ts_n.to_period("M").to_timestamp()}[freq]
        return float(g.get(bucket, 0.0))

    def _block_key(self, ts: pd.Timestamp) -> str:
        return f"{ts.floor('D').date()}T{(ts.hour // 4) * 4:02d}"

    def _block_pnl(self, ts: pd.Timestamp) -> float:
        if not self.ledger:
            return 0.0
        df = pd.DataFrame(self.ledger).set_index("ts").sort_index()
        keys = pd.Series([self._block_key(t) for t in df.index], index=df.index)
        return float(df.groupby(keys)["pnl"].sum().get(self._block_key(ts), 0.0))

    def allow_entry(self, now_ts: datetime | pd.Timestamp,
                    equity: float | None = None) -> tuple[bool, str]:
        """Hard survival gates. Returns (allowed, reason)."""
        c = self.cfg
        ts = pd.Timestamp(now_ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        eq = equity if equity is not None else self._current_equity()
        if eq <= 0:
            return False, "equity exhausted"

        for freq, label, limit in (("D", "daily", c.daily_stop_pct),
                                   ("W", "weekly", c.weekly_stop_pct),
                                   ("M", "monthly", c.monthly_stop_pct)):
            pnl = self._window_pnl(ts, freq)
            if pnl <= -abs(limit) / 100.0 * eq:
                return False, f"{label} stop hit ({pnl:+.2f} <= -{limit}% of equity)"

        if self._block_pnl(ts) >= c.session_target_usd:
            return False, (f"session target hit "
                           f"(+${self._block_pnl(ts):.2f} >= ${c.session_target_usd:.0f} "
                           f"in 4h block {self._block_key(ts)})")

        curve = self._cum_equity_curve()
        if len(curve) >= c.equity_ma_window:
            if eq < float(curve.rolling(c.equity_ma_window).mean().iloc[-1]):
                return False, ("equity curve below its own "
                               f"{c.equity_ma_window}-trade mean - standing down")
        return True, ""

    def _current_equity(self) -> float:
        return float(self._cum_equity_curve().iloc[-1]) if self.ledger \
            else self.starting_equity

    # ------------------------------------------------------------ utility
    def snapshot(self) -> dict:
        curve = self._cum_equity_curve()
        return {
            "closed_trades": len(self.ledger),
            "loss_streak": self._streak_losses,
            "kelly_cap_pct": self.kelly_cap_pct(),
            "equity": self._current_equity(),
            "equity_ma50": float(curve.rolling(self.cfg.equity_ma_window)
                                 .mean().iloc[-1]) if len(curve) >= 5 else None,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
