"""
GOLD REAPER APEX :: APEX-X Ensemble Strategy
============================================
Four specialist modules vote; a regime router decides WHO is allowed to
speak; the meta-model casts a soft vote; the ensemble requires consensus:

    TREND module     -> speaks in TREND_UP / TREND_DOWN   (Reaper-X pullback)
    MEANREV module   -> speaks in RANGE                    (BB + RSI + z-score)
    BREAKOUT module  -> speaks in VOLATILE_CHOP            (Donchian + flow)
    NEWS module      -> speaks 5..30 min after gold-critical events (live only)
    META-MODEL       -> soft vote ±0.5, hard gate only if OOS AUC >= 0.56

Ensemble rule: weighted votes >= 2.5 AND >= 2 rule modules agree on side.
Crisis regime: nobody speaks. The reaper goes quiet and survives.

Every emitted signal carries entry, SL, TP1/TP2/TP3, expected R:R,
confidence, and a full reasoning trace (audited by core/audit.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from core.config import CONFIG, Config
from core.news_brain import NewsState
from core.regime import RegimeState
from core.sessions import in_blackout, session_of
from core.strategy import ReaperX, Signal

TREND_REGIMES = {"TREND_UP", "TREND_DOWN"}
RISK_WEIGHT = 1.0
ML_SOFT_WEIGHT = 0.5
ENSEMBLE_MIN_WEIGHT = 2.5
MIN_MODULES_AGREE = 2


@dataclass
class ApexSignal:
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    atr: float
    session: str
    ts: datetime
    confidence: float
    votes: dict
    regime: str
    reasoning: list
    ml_prob: float | None = None

    @property
    def tp(self) -> float:
        """Primary target used for order placement (TP2 = 2R)."""
        return self.tp2

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def expected_rr(self) -> float:
        return abs(self.tp2 - self.entry) / self.risk_distance if self.risk_distance else 0.0


class ApexX:
    name = "APEX-X"

    def __init__(self, cfg: Config | None = None,
                 ml_auc: float | None = None) -> None:
        self.cfg = cfg or CONFIG
        self.reaper = ReaperX(self.cfg)
        self.ml_auc = ml_auc
        self.ml_hard_gate = bool(ml_auc and ml_auc >= 0.56)

    # ------------------------------------------------------------ modules
    def _trend(self, h1: pd.DataFrame, h4: pd.DataFrame, ts) -> Signal | None:
        try:
            return self.reaper.evaluate(h1, h4, ts)
        except Exception:  # noqa: BLE001
            return None

    def _meanrev(self, row: dict, atr_v: float) -> Signal | None:
        c = self.cfg
        bb_pos = row.get("f_bb_pos")
        rsi_v = row.get("f_rsi_14")
        z = row.get("f_zscore_100")
        close = row.get("close")
        ts_v = row.get("ts")
        if any(v is None or not isinstance(v, (int, float)) or np.isnan(v)
               for v in (bb_pos, rsi_v, z, close)):
            return None
        if ts_v is None:
            return None
        assert isinstance(bb_pos, float) and isinstance(rsi_v, float)
        assert isinstance(z, float) and isinstance(close, float)
        if bb_pos <= 0.05 and rsi_v < 30 and z < -1.5:
            entry = float(close)
            sl = entry - c.sl_atr_mult * atr_v
            return Signal("LONG", entry, sl, entry + (entry - sl), atr_v,
                          f"meanrev: bb_pos {bb_pos:.2f} rsi {rsi_v:.0f} z {z:.1f}",
                          "RANGE", ts_v, float(rsi_v))
        if bb_pos >= 0.95 and rsi_v > 70 and z > 1.5:
            entry = float(close)
            sl = entry + c.sl_atr_mult * atr_v
            return Signal("SHORT", entry, sl, entry - (sl - entry), atr_v,
                          f"meanrev: bb_pos {bb_pos:.2f} rsi {rsi_v:.0f} z {z:.1f}",
                          "RANGE", ts_v, float(rsi_v))
        return None

    def _breakout(self, row: dict, atr_v: float,
                  recent_range: float | None) -> Signal | None:
        close = row.get("close")
        brk_up = row.get("f_donch_break_up")
        brk_dn = row.get("f_donch_break_dn")
        ofi = row.get("f_ofi_12")
        volz = row.get("f_vol_z")
        if close is None or brk_up is None:
            return None
        flow_ok = (ofi is not None and not np.isnan(ofi)) and abs(ofi) > 0.05
        vol_ok = (volz is not None and not np.isnan(volz)) and volz > 0.5
        rng_ok = recent_range is not None and recent_range > 0
        if not (flow_ok and vol_ok and rng_ok):
            return None
        ts = row.get("ts")
        if ts is None:
            return None
        if brk_up == 1 and (ofi or 0) > 0:
            entry = float(close)
            sl = entry - self.cfg.sl_atr_mult * atr_v
            return Signal("LONG", entry, sl, entry + (entry - sl), atr_v,
                          f"breakout: donchian-up ofi {ofi:.2f} volz {volz:.1f}",
                          "VOLATILE_CHOP", ts)
        if brk_dn == 1 and (ofi or 0) < 0:
            entry = float(close)
            sl = entry + self.cfg.sl_atr_mult * atr_v
            return Signal("SHORT", entry, sl, entry - (sl - entry), atr_v,
                          f"breakout: donchian-dn ofi {ofi:.2f} volz {volz:.1f}",
                          "VOLATILE_CHOP", ts)
        return None

    def _news(self, row: dict, atr_v: float, news: NewsState) -> Signal | None:
        """Post-event momentum: trade the REACTION 5-30 min after a
        gold-critical high-impact release, in the direction of measured
        sentiment. Live-only dimension (historical calendar not reproducible)."""
        if news.last_event_relevance < 0.6:
            return None
        if not (5 <= news.minutes_since_event <= 30):
            return None
        if abs(news.sentiment) < 0.4 or news.sentiment_confidence < 0.3:
            return None
        close = row.get("close")
        if close is None:
            return None
        ts = row.get("ts")
        if ts is None:
            return None
        entry = float(close)
        side = "LONG" if news.sentiment > 0 else "SHORT"
        if side == "LONG":
            sl = entry - 1.0 * atr_v          # tighter risk on news
            tp = entry + 2.0 * atr_v
        else:
            sl = entry + 1.0 * atr_v
            tp = entry - 2.0 * atr_v
        return Signal(side, entry, sl, tp, atr_v,
                      f"news: '{news.last_event[:40]}' sent {news.sentiment:+.2f}",
                      "NEWS", ts)

    # ------------------------------------------------------------ ensemble
    def evaluate(self, h1: pd.DataFrame, h4: pd.DataFrame, ts,
                 feat_row: dict | None, regime: RegimeState,
                 news: NewsState, ml_prob: float | None = None) -> ApexSignal | None:
        c = self.cfg
        sess = session_of(ts)
        if c.session_windows.get(sess) is None:
            return None
        if in_blackout(ts, c.blackout_hours_utc):
            return None
        if regime.regime == "CRISIS":
            return None                       # nobody hunts in a storm
        # v2.3 hardened hunt window (walk-forward: hours 14-15 UTC bleed)
        if c.entry_hours_utc and ts.hour not in c.entry_hours_utc:
            return None

        # last-bar context
        row = dict(feat_row or {})
        row["ts"] = ts
        row["close"] = float(h1["close"].iloc[-1])
        a_norm_v = row.get("f_atr_14_norm", 0)
        atr_v = (float(a_norm_v) if isinstance(a_norm_v, (int, float))
                 else 0.0) * float(row["close"])
        if atr_v <= 0:
            from core.indicators import atr as atr_fn
            atr_v = float(atr_fn(h1, 14).iloc[-1])
        if atr_v < c.min_atr_price:
            return None

        recent_range = None
        if len(h1) > 12:
            hi12 = float(h1["high"].iloc[-12:].max())
            lo12 = float(h1["low"].iloc[-12:].min())
            recent_range = (hi12 - lo12) / row["close"]

        votes: dict[str, str] = {}
        signals: dict[str, Signal] = {}
        trace: list[str] = []

        # 1. trend module
        if regime.regime in TREND_REGIMES:
            sig = self._trend(h1, h4, ts)
            if sig is not None:
                votes["trend"] = sig.side
                signals["trend"] = sig
                trace.append(f"trend: {sig.reason}")
            else:
                trace.append("trend: silent (no clean pullback)")

        # 2. mean-reversion module (v2.3: only in low-vol tape, rank < ceiling)
        if regime.regime == "RANGE":
            vol_rank = self._vol_rank(h1)
            vr_ok = (c.meanrev_vol_max is None or
                     (vol_rank is not None and vol_rank < c.meanrev_vol_max))
            sig = self._meanrev(row, atr_v) if vr_ok else None
            if sig is not None:
                votes["meanrev"] = sig.side
                signals["meanrev"] = sig
                trace.append(sig.reason)
            else:
                trace.append("meanrev: bands calm" if vr_ok else
                             f"meanrev: vol gate (rank {vol_rank if vol_rank is not None else 'NA'})")

        # 3. breakout module
        if regime.regime == "VOLATILE_CHOP":
            sig = self._breakout(row, atr_v, recent_range)
            if sig is not None:
                votes["breakout"] = sig.side
                signals["breakout"] = sig
                trace.append(sig.reason)
            else:
                trace.append("breakout: no expansion")

        # 4. news module (live dimension)
        if news.risk_window is False and news.minutes_since_event <= 60:
            sig = self._news(row, atr_v, news)
            if sig is not None:
                votes["news"] = sig.side
                signals["news"] = sig
                trace.append(sig.reason)

        if not votes:
            return None

        # consensus
        weight_long = sum(RISK_WEIGHT for s in votes.values() if s == "LONG")
        weight_short = sum(RISK_WEIGHT for s in votes.values() if s == "SHORT")
        if ml_prob is not None:
            if ml_prob >= 0.55:
                weight_long += ML_SOFT_WEIGHT
                trace.append(f"meta-model: soft vote LONG ({ml_prob:.2f})")
            elif ml_prob <= 0.45:
                weight_short += ML_SOFT_WEIGHT
                trace.append(f"meta-model: soft vote SHORT ({1 - ml_prob:.2f})")
            else:
                trace.append(f"meta-model: neutral ({ml_prob:.2f})")

        sides = list(votes.values())
        dominant = "LONG" if weight_long > weight_short else "SHORT"
        agree = sum(1 for s in sides if s == dominant)
        weight = weight_long if dominant == "LONG" else weight_short
        if weight < ENSEMBLE_MIN_WEIGHT or agree < MIN_MODULES_AGREE:
            trace.append(f"ensemble: no consensus ({dominant} w={weight:.1f} "
                         f"agree={agree})")
            self._last_trace = trace
            self._last_votes = votes
            return None

        # hard ML gate (only when a production model proved OOS AUC >= 0.56)
        if self.ml_hard_gate and ml_prob is not None:
            need = 0.65 if dominant == "LONG" else 0.35
            if (dominant == "LONG" and ml_prob < need) or \
               (dominant == "SHORT" and ml_prob > need):
                trace.append(f"meta-model HARD VETO (prob {ml_prob:.2f})")
                self._last_trace = trace
                self._last_votes = votes
                return None

        base = signals.get("trend") or signals.get("breakout") \
            or signals.get("meanrev") or signals.get("news")
        if base is None:
            return None

        entry = base.entry
        sl = base.sl
        r_dist = abs(entry - sl)
        # TP ladder aligned with walk-forward-locked geometry (v2.3: TP = 2.0R)
        if dominant == "LONG":
            tp1, tp2, tp3 = entry + r_dist, entry + c.tp_r * r_dist, entry + 4.0 * r_dist
        else:
            tp1, tp2, tp3 = entry - r_dist, entry - c.tp_r * r_dist, entry - 4.0 * r_dist

        conf = min(1.0, 0.35 + 0.15 * agree + (0.2 if ml_prob and
                                                 ((dominant == "LONG" and ml_prob >= 0.55) or
                                                  (dominant == "SHORT" and ml_prob <= 0.45)) else 0)
                   + 0.1 * regime.probability)
        trace.append(f"ENSEMBLE KILL: {dominant} w={weight:.1f} agree={agree}")

        return ApexSignal(dominant, entry, sl, tp1, tp2, tp3, atr_v, sess, ts,
                          conf, votes, regime.regime, trace, ml_prob)

    _last_trace: list[str] = []
    _last_votes: dict[str, str] = {}

    @staticmethod
    def _vol_rank(h1: pd.DataFrame) -> float | None:
        """ATR-percentile of the last closed bar (0..1), matching the backtest
        engine's vol definition (rolling 500-bar rank of normalized ATR)."""
        try:
            from core.indicators import atr as atr_fn
            a = atr_fn(h1, 14) / h1["close"]
            v = a.rolling(500, min_periods=100).rank(pct=True).iloc[-1]
            return None if pd.isna(v) else float(v)
        except Exception:  # noqa: BLE001
            return None
