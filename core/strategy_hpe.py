"""
GOLD REAPER HPE :: High-Probability Engine Strategy
====================================================
Phase 3 of the HPE build. A filtered, high-conviction strategy that:

  - only trades when >= 4 of 8 independent ensemble modules agree
  - requires psychological alignment (no fighting capitulation/euphoria)
  - requires micro-structure confirmation from bar anatomy + path metrics
  - requires regime fit (never CRISIS) and session fit (measured hours)
  - uses tight, evidence-based geometry: TP 2.0-2.5R, SL 1.2 x ATR

Same broker-agnostic interface as REAPER-X (evaluate / manage_exit) so the
bot drives it identically. The strategy NEVER fires on quiet bars - days
with zero trades are a feature, not a bug.

HPE config is read from env here (core/config.py is byte-frozen):
    HPE_MIN_AGREEMENT   default 4        (of 8 modules)
    HPE_MIN_CONFIDENCE  default 0.40     (degenerate-alignment floor; the
                                          >=4 agreement rule is the lever)
    HPE_ENTRY_HOURS     default "12,13,14,15"  (UTC, measured overlap window)
    HPE_SL_ATR          default 1.2
    HPE_TP_R            default 2.2      (kept inside the 2.0-2.5R evidence band)
    HPE_ML_HARD         default false    (ML is a soft vote unless promoted)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from core.ensemble_hpe import Decision, EnsembleHPE
from core.indicators import adx, atr, ema, rsi
from core.psychology import PsychologyEngine
from core.regime import RegimeDetector
from core.sessions import session_of
from core.strategy import Signal


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_hours(key: str, default: str) -> tuple:
    raw = os.environ.get(key, default)
    try:
        hours = tuple(sorted({int(x) for x in raw.split(",") if x.strip() != ""}))
        return hours or tuple(sorted({int(x) for x in default.split(",")}))
    except (TypeError, ValueError):
        return tuple(sorted({int(x) for x in default.split(",")}))


@dataclass
class HPEConfig:
    min_agreement: int = 4
    min_confidence: float = 0.40
    entry_hours_utc: tuple = (12, 13, 14, 15)
    hours_explicit: bool = False      # True when HPE_ENTRY_HOURS set in env
    sl_atr_mult: float = 1.2
    tp_r: float = 2.2
    breakeven_at_r: float = 1.0
    trail_start_r: float = 1.5
    trail_atr_mult: float = 1.2
    ml_hard: bool = False

    @classmethod
    def from_env(cls) -> "HPEConfig":
        tp_r = _env_float("HPE_TP_R", 2.2)
        tp_r = float(np.clip(tp_r, 2.0, 2.5))   # evidence band, non-negotiable
        explicit = "HPE_ENTRY_HOURS" in os.environ
        return cls(
            min_agreement=max(2, _env_int("HPE_MIN_AGREEMENT", 4)),
            min_confidence=_env_float("HPE_MIN_CONFIDENCE", 0.40),
            entry_hours_utc=_env_hours("HPE_ENTRY_HOURS", "12,13,14,15"),
            hours_explicit=explicit,
            sl_atr_mult=_env_float("HPE_SL_ATR", 1.2),
            tp_r=tp_r,
            ml_hard=os.environ.get("HPE_ML_HARD", "false").lower() in ("1", "true", "yes"),
        )


@dataclass
class HPEState:
    last_decision: Decision | None = None
    last_signal_ts: pd.Timestamp | None = None


class StrategyHPE:
    """The patient hunter. Refuses almost everything, strikes with the crowd wrong."""

    name = "HPE"

    def __init__(self, cfg: HPEConfig | None = None,
                 ensemble: EnsembleHPE | None = None) -> None:
        self.cfg = cfg or HPEConfig.from_env()
        self.psychology = PsychologyEngine()
        self.ensemble = ensemble or EnsembleHPE(
            psychology=self.psychology,
            min_agreement=self.cfg.min_agreement,
            min_confidence=self.cfg.min_confidence,
            ml_hard=self.cfg.ml_hard)
        self.state = HPEState()

    # ------------------------------------------------------------- helpers
    @staticmethod
    def h4_bias(h4: pd.DataFrame) -> str:
        close = float(h4["close"].iloc[-1])
        f50 = float(ema(h4["close"], min(50, max(3, len(h4) // 2))).iloc[-1])
        f200 = float(ema(h4["close"], min(200, max(4, len(h4) - 1))).iloc[-1])
        if close > f50 > f200:
            return "LONG"
        if close < f50 < f200:
            return "SHORT"
        return "NEUTRAL"

    @staticmethod
    def _live_inputs(h1: pd.DataFrame) -> dict:
        """Compute everything the ensemble needs from a raw h1 tail window."""
        c = h1["close"]
        a14 = float(atr(h1, 14).iloc[-1])
        r14 = float(rsi(c, 14).iloc[-1])
        r14p = float(rsi(c, 14).iloc[-2])
        dx = float(adx(h1, 14).iloc[-1])
        e20 = float(ema(c, 20).iloc[-1])
        up = c.rolling(100).mean()
        sd = c.rolling(100).std()
        z100 = float(((c.iloc[-1] - up.iloc[-1]) / sd.iloc[-1]) if sd.iloc[-1] else np.nan)
        bb_m = c.rolling(20).mean()
        bb_sd = c.rolling(20).std()
        bb_up = bb_m + 2 * bb_sd
        bb_lo = bb_m - 2 * bb_sd
        width = (bb_up - bb_lo).iloc[-1]
        bb_pos = float((c.iloc[-1] - bb_lo.iloc[-1]) / width) if width else np.nan
        hh = h1["high"].rolling(20).max().shift(1)
        ll = h1["low"].rolling(20).min().shift(1)
        donch_up = 1.0 if c.iloc[-1] > hh.iloc[-1] else 0.0
        donch_dn = 1.0 if c.iloc[-1] < ll.iloc[-1] else 0.0
        up_v = h1["volume"].where(h1["close"] >= h1["open"], 0.0).rolling(12).sum()
        dn_v = h1["volume"].where(h1["close"] < h1["open"], 0.0).rolling(12).sum()
        ofi = float(((up_v - dn_v) / (up_v + dn_v).replace(0, np.nan)).iloc[-1])
        vol_z = float(((h1["volume"] - h1["volume"].rolling(100).mean())
                       / h1["volume"].rolling(100).std().replace(0, np.nan)).iloc[-1])
        ind = {"rsi": r14, "rsi_prev": r14p, "adx": dx, "atr": a14, "e20": e20,
               "bb_pos": bb_pos, "z100": z100, "donch_up": donch_up,
               "donch_dn": donch_dn, "ofi": ofi, "vol_z": vol_z}
        return ind

    # ------------------------------------------------------------- evaluate
    def evaluate(self, h1: pd.DataFrame, h4: pd.DataFrame, ts: datetime,
                 micro_frame: pd.DataFrame | None = None,
                 psy_frame: pd.DataFrame | None = None,
                 cross: dict | None = None,
                 news: dict | None = None,
                 ml_prob: float | None = None,
                 ml_promoted: bool = False,
                 regime_override: str | None = None) -> Signal | None:
        if len(h1) < 300 or len(h4) < 60:
            return None

        sess = session_of(ts)
        # window = measured hours when explicitly set (session label is
        # informational); default = repo's hardened overlap window
        if ts.hour not in self.cfg.entry_hours_utc:
            return None
        if not self.cfg.hours_explicit and sess != "LONDON_NY_OVERLAP":
            return None
        if ts.weekday() == 4 and ts.hour >= 18:
            return None

        if regime_override is not None:
            regime = regime_override
        else:
            detector = RegimeDetector()
            regime = detector.classify(h1.tail(600)).regime

        ind = self._live_inputs(h1)
        if not ind["atr"] or np.isnan(ind["atr"]):
            return None

        window = h1.tail(600)
        if micro_frame is not None and len(micro_frame) == len(h1):
            m_row = micro_frame.loc[ts] if ts in micro_frame.index else None
        else:
            from features.micro_features import compute_micro_features
            m_row = compute_micro_features(window).iloc[-1]
        if psy_frame is not None and len(psy_frame) == len(h1):
            p_row = psy_frame.loc[ts] if ts in psy_frame.index else None
        else:
            p_row = self.psychology.compute_frame(window).iloc[-1]

        ctx = {
            "ts": pd.Timestamp(ts), "close": float(h1["close"].iloc[-1]),
            "open": float(h1["open"].iloc[-1]), "high": float(h1["high"].iloc[-1]),
            "low": float(h1["low"].iloc[-1]), "ind": ind,
            "h4_bias": self.h4_bias(h4), "regime": regime, "session": sess,
            "psy_row": p_row, "micro_row": m_row, "cross": cross or {},
            "news": news or {}, "ml_prob": ml_prob, "ml_promoted": ml_promoted,
        }
        decision = self.ensemble.decide(ctx)
        self.state.last_decision = decision
        if not decision.accepted or decision.side is None:
            return None

        px = float(h1["close"].iloc[-1])
        atr_v = ind["atr"]
        sl_dist = self.cfg.sl_atr_mult * atr_v
        if decision.side == "LONG":
            entry, sl = px, px - sl_dist
            tp = entry + self.cfg.tp_r * sl_dist
        else:
            entry, sl = px, px + sl_dist
            tp = entry - self.cfg.tp_r * sl_dist

        sig = Signal(decision.side, entry, sl, tp, atr_v,
                     f"HPE {decision.aligned}/8 conf {decision.confidence:.2f} "
                     f"regime {regime}", sess, ts, ind["rsi"], ind["adx"])
        sig.meta = {"decision": decision.to_dict(), "confidence": decision.confidence,
                    "votes": decision.votes, "explain": decision.explain,
                    "hpe_tp_r": self.cfg.tp_r}
        # bot._execute/_journal read these APEX-style attributes; the frozen
        # core.strategy.Signal does not define them, so attach dynamically
        # (setattr keeps mypy quiet about unknown attrs on a frozen file's type)
        setattr(sig, "confidence", decision.confidence)
        setattr(sig, "regime", regime)
        setattr(sig, "votes", {m: v["dir"] for m, v in decision.votes.items()})
        self.state.last_signal_ts = h1.index[-1]
        return sig

    # ------------------------------------------------------- open trade mng
    def manage_exit(self, pos: dict, bar_high: float, bar_low: float,
                    atr_v: float) -> tuple[str, float | None]:
        """Same contract as REAPER-X.manage_exit, HPE geometry."""
        c = self.cfg
        entry, sl, tp = pos["entry"], pos["sl"], pos["tp"]
        side = pos["side"]
        # r_dist is the ORIGINAL risk distance. After a BE move the live SL is
        # near entry, so the distance must come from orig_sl (the ORIGINAL SL
        # price). HPE deliberately does NOT inherit the frozen REAPER-X
        # expression `abs(entry - sl if be_moved else orig_sl)` which returns
        # the orig_sl PRICE (~98.8) instead of the DISTANCE (~1.2) and deadens
        # the trail after breakeven - documented as SD-7, unfixed by freeze.
        if pos.get("be_moved") and pos.get("orig_sl"):
            r_dist = abs(entry - float(pos["orig_sl"]))
        else:
            r_dist = abs(entry - sl)

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
