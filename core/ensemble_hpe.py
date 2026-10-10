"""
GOLD REAPER HPE :: 8-Module Ensemble Voting — the win-rate lever
================================================================
Phase 5 of the High-Probability Edge engine. Eight independent modules
vote on every bar; a trade only fires when >= 4 agree on direction AND
psychology raises no veto AND the regime fits AND the session fits.

    trend        REAPER-X pullback-continuation conditions (frozen logic, mirrored)
    meanrev      RANGE regime band-touch reversion (bb_pos / RSI / z-score)
    breakout     DONCHIAN break + order-flow imbalance in active regimes
    psychology   capitulation / euphoria / composite extremes (sentiment contrarian)
    micro        candle anatomy + sequencing + path metrics confirm the lean
    cross_asset  gold residual vs DXY/US10Y/SILVER/BTC stretches
    news         headline sentiment reaction (abstains without data - honestly)
    ml           meta-model probability (soft vote; hard vote only if promoted)

Voting rule (the win-rate lever): refuse to trade unless many independent
signals agree. Fewer, cleaner trades. Days with zero trades are correct.

Every decision carries a full explanation: which modules voted, why, and
which gates passed or failed - the explain mode reads exactly this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.psychology import PsychologyEngine

MODULES = ("trend", "meanrev", "breakout", "psychology",
           "micro", "cross_asset", "news", "ml")


@dataclass
class Vote:
    module: str
    direction: int          # -1 SHORT, 0 abstain, +1 LONG
    strength: float = 0.0   # 0..1 conviction
    reason: str = ""


@dataclass
class Decision:
    side: str | None = None          # "LONG" | "SHORT" | None
    confidence: float = 0.0          # 0..1 ensemble confidence (heuristic, documented)
    aligned: int = 0                 # modules voting the candidate side
    psychology: dict | None = None   # fear/greed summary at decision time
    required: int = 4
    votes: dict[str, dict] = field(default_factory=dict)
    vetoes: list[str] = field(default_factory=list)
    regime: str = "RANGE"
    session: str = "ASIA"
    explain: list[str] = field(default_factory=list)
    accepted: bool = False

    def to_dict(self) -> dict:
        out = {"side": self.side, "confidence": round(self.confidence, 4),
                "aligned": self.aligned, "required": self.required,
                "votes": self.votes, "vetoes": self.vetoes,
                "regime": self.regime, "session": self.session,
                "explain": self.explain, "accepted": self.accepted}
        if self.psychology is not None:
            out["psychology"] = self.psychology
        return out


class EnsembleHPE:
    """Requires >= min_agreement of 8 modules plus zero vetoes."""

    name = "HPE-ENSEMBLE"

    def __init__(self, psychology: PsychologyEngine | None = None,
                 min_agreement: int = 4,
                 regime_allow: tuple = ("TREND_UP", "TREND_DOWN", "RANGE",
                                        "VOLATILE_CHOP"),
                 sessions_allow: tuple = ("LONDON_NY_OVERLAP",),
                 min_confidence: float = 0.40,
                 ml_hard: bool = False) -> None:
        self.psychology = psychology or PsychologyEngine()
        self.min_agreement = min_agreement
        self.regime_allow = tuple(regime_allow)
        self.sessions_allow = tuple(sessions_allow)
        self.min_confidence = min_confidence
        self.ml_hard = ml_hard

    # ---------------------------------------------------------------- votes
    def _v_trend(self, ctx: dict) -> Vote:
        """REAPER-X conditions, mirrored read-only from the frozen engine."""
        bias = ctx.get("h4_bias", "NEUTRAL")
        ind = ctx.get("ind", {})
        rsi, adx, atr = ind.get("rsi", np.nan), ind.get("adx", np.nan), ind.get("atr", 0.0)
        close, e20 = ctx.get("close", np.nan), ind.get("e20", np.nan)
        if any(np.isnan(v) for v in (rsi, adx, close, e20)) or not atr:
            return Vote("trend", 0, 0.0, "indicators cold")
        near = abs(close - e20) <= 0.6 * atr
        if adx < 24 or not near:
            return Vote("trend", 0, 0.0, f"adx {adx:.0f} near_ema={near}")
        body_up = ctx.get("close", 0) > ctx.get("open", 0)
        rsi_prev = ind.get("rsi_prev", np.nan)
        if bias == "LONG" and body_up and 38 <= rsi <= 52 and rsi >= rsi_prev:
            return Vote("trend", 1, float(np.clip((adx - 24) / 30, 0.2, 1.0)),
                        f"bias-up pullback rsi {rsi:.0f} adx {adx:.0f}")
        if bias == "SHORT" and not body_up and 48 <= rsi <= 62 and rsi <= rsi_prev:
            return Vote("trend", -1, float(np.clip((adx - 24) / 30, 0.2, 1.0)),
                        f"bias-dn pullback rsi {rsi:.0f} adx {adx:.0f}")
        return Vote("trend", 0, 0.0, "no pullback trigger")

    def _v_meanrev(self, ctx: dict) -> Vote:
        if ctx.get("regime") != "RANGE":
            return Vote("meanrev", 0, 0.0, f"regime {ctx.get('regime')}")
        ind = ctx.get("ind", {})
        bb, rsi, z = ind.get("bb_pos", np.nan), ind.get("rsi", np.nan), ind.get("z100", np.nan)
        if any(np.isnan(v) for v in (bb, rsi, z)):
            return Vote("meanrev", 0, 0.0, "meanrev inputs cold")
        if bb <= 0.05 and rsi < 30 and z < -1.5:
            return Vote("meanrev", 1, float(np.clip(-z / 3, 0.3, 1.0)),
                        f"band-low bb {bb:.2f} rsi {rsi:.0f} z {z:.1f}")
        if bb >= 0.95 and rsi > 70 and z > 1.5:
            return Vote("meanrev", -1, float(np.clip(z / 3, 0.3, 1.0)),
                        f"band-high bb {bb:.2f} rsi {rsi:.0f} z {z:.1f}")
        return Vote("meanrev", 0, 0.0, "mid-band")

    def _v_breakout(self, ctx: dict) -> Vote:
        if ctx.get("regime") not in ("VOLATILE_CHOP", "TREND_UP", "TREND_DOWN"):
            return Vote("breakout", 0, 0.0, f"regime {ctx.get('regime')}")
        ind = ctx.get("ind", {})
        bu, bd = ind.get("donch_up", 0.0), ind.get("donch_dn", 0.0)
        ofi, volz = ind.get("ofi", np.nan), ind.get("vol_z", np.nan)
        if np.isnan(ofi) or np.isnan(volz):
            return Vote("breakout", 0, 0.0, "flow inputs cold")
        if volz < 0.3 or abs(ofi) <= 0.05:
            return Vote("breakout", 0, 0.0, "flow quiet")
        if bu == 1 and ofi > 0:
            return Vote("breakout", 1, float(np.clip(volz / 3, 0.3, 1.0)),
                        f"donchian up ofi {ofi:.2f}")
        if bd == 1 and ofi < 0:
            return Vote("breakout", -1, float(np.clip(volz / 3, 0.3, 1.0)),
                        f"donchian dn ofi {ofi:.2f}")
        return Vote("breakout", 0, 0.0, "no confirmed break")

    def _v_psychology(self, ctx: dict) -> Vote:
        """Sentiment-contrarian: panic buys, euphoria fades the crowd."""
        row = ctx.get("psy_row")
        if row is None or not hasattr(row, "get"):
            return Vote("psychology", 0, 0.0, "psy unavailable")
        cap, eup = float(row.get("psy_capitulation", 0.0) or 0.0), \
            float(row.get("psy_euphoria", 0.0) or 0.0)
        fg = row.get("psy_fear_greed", np.nan)
        if eup == 1.0:
            return Vote("psychology", -1, 0.8, "euphoria climax - fade the crowd")
        if cap == 1.0:
            return Vote("psychology", 1, 0.8, "capitulation flush - fade the panic")
        if not np.isnan(fg):
            if fg <= 30:
                return Vote("psychology", 1, 0.5, f"fear zone {fg:.0f}")
            if fg >= 70:
                return Vote("psychology", -1, 0.5, f"greed zone {fg:.0f}")
        return Vote("psychology", 0, 0.0,
                    f"psy neutral fg {fg:.0f}" if not np.isnan(fg) else "psy neutral")

    def _v_micro(self, ctx: dict, lean: int) -> Vote:
        """Micro-structure CONFIRMATION (Phase 3: 'anatomy + sequencing + path
        metrics must confirm'). Confirms the pre-lean of the other modules
        when candle anatomy agrees with it - it does not invent direction."""
        m = ctx.get("micro_row")
        if m is None or not hasattr(m, "get"):
            return Vote("micro", 0, 0.0, "micro unavailable")
        if lean == 0:
            return Vote("micro", 0, 0.0, "no lean to confirm")
        vel = m.get("mf_velocity", np.nan)
        er = m.get("mf_er_20", np.nan)
        cpos = m.get("mf_close_pos", 0.5)
        if np.isnan(vel):
            return Vote("micro", 0, 0.0, "micro cold")
        if lean > 0:
            bull = (float(m.get("mf_engulf_bull", 0) or 0)
                    or float(m.get("mf_hammer", 0) or 0)
                    or float(m.get("mf_three_soldiers", 0) or 0)
                    or float(m.get("mf_pin_bull", 0) or 0))
            # full Phase-1 anatomy set: reversal-continuation patterns OR a
            # neutral-structure bar (doji / inside / harami) that CLOSES in
            # the lean's half of the range while velocity has stopped falling
            neutral = (float(m.get("mf_doji", 0) or 0)
                       or float(m.get("mf_doji_dragonfly", 0) or 0)
                       or float(m.get("mf_inside", 0) or 0)
                       or float(m.get("mf_harami_bull", 0) or 0))
            confirmed = bull or (neutral and cpos >= 0.6)
            # reversal bars close inside the range while velocity is still
            # negative - accept that, but never a bar closing hard at its low
            ok_path = (vel >= -0.5) and (not np.isnan(er) and er >= -0.1
                                         or cpos >= 0.5)
            if confirmed and ok_path:
                return Vote("micro", 1, float(np.clip(0.4 + 0.2 * int(bull),
                                                      0.4, 0.9)),
                            f"bull anatomy confirms lean (vel {vel:.2f})")
            return Vote("micro", 0, 0.0, "bull anatomy absent")
        bear = (float(m.get("mf_engulf_bear", 0) or 0)
                or float(m.get("mf_shooting_star", 0) or 0)
                or float(m.get("mf_three_crows", 0) or 0)
                or float(m.get("mf_pin_bear", 0) or 0))
        neutral = (float(m.get("mf_doji", 0) or 0)
                   or float(m.get("mf_doji_gravestone", 0) or 0)
                   or float(m.get("mf_inside", 0) or 0)
                   or float(m.get("mf_harami_bear", 0) or 0))
        confirmed = bear or (neutral and cpos <= 0.4)
        ok_path = (vel <= 0.5) and (not np.isnan(er) and er >= -0.1
                                    or cpos <= 0.5)
        if confirmed and ok_path:
            return Vote("micro", -1, float(np.clip(0.4 + 0.2 * int(bear),
                                                   0.4, 0.9)),
                        f"bear anatomy confirms lean (vel {vel:.2f})")
        return Vote("micro", 0, 0.0, "bear anatomy absent")

    def _v_cross_asset(self, ctx: dict) -> Vote:
        """Gold residual vs its cross-asset complex stretched -> reversion vote."""
        cross = ctx.get("cross") or {}
        resid_z = cross.get("f_resid_z_DXY", np.nan)
        corr = cross.get("f_corr_DXY", np.nan)
        if np.isnan(resid_z):
            resid_z = cross.get("f_resid_z_SILVER", np.nan)
            corr = cross.get("f_corr_SILVER", np.nan)
            sign_flip = False            # silver correlates POSITIVELY with gold
        else:
            sign_flip = True             # DXY correlates NEGATIVELY with gold
        if np.isnan(resid_z):
            return Vote("cross_asset", 0, 0.0, "cross-asset cold")
        # resid_z > 0 => gold rich vs the anchor. For a negatively-correlated
        # anchor (DXY) rich gold + cheap dollar implies long-side stretch.
        stretch = resid_z if sign_flip else -resid_z
        if stretch > 1.2 and (np.isnan(corr) or abs(corr) > 0.2):
            return Vote("cross_asset", -1, float(np.clip(stretch / 3, 0.3, 0.9)),
                        f"gold rich vs anchor z {resid_z:.1f}")
        if stretch < -1.2 and (np.isnan(corr) or abs(corr) > 0.2):
            return Vote("cross_asset", 1, float(np.clip(-stretch / 3, 0.3, 0.9)),
                        f"gold cheap vs anchor z {resid_z:.1f}")
        return Vote("cross_asset", 0, 0.0, f"resid z {resid_z:.1f} in band")

    def _v_news(self, ctx: dict) -> Vote:
        """Headline reaction. Abstains (honestly) when no sentiment source."""
        news = ctx.get("news") or {}
        sent, conf = news.get("sent", 0.0), news.get("conf", 0.0)
        if news.get("min_to_event", 9999.0) <= 30 and news.get("relevance", 0.0) >= 0.6:
            return Vote("news", 0, 0.0, "event window - standing down")
        if conf >= 0.5 and abs(sent) >= 0.4:
            return Vote("news", 1 if sent > 0 else -1, float(np.clip(conf, 0.3, 0.9)),
                        f"headline sent {sent:+.2f} conf {conf:.2f}")
        return Vote("news", 0, 0.0, "no readable headline signal")

    def _v_ml(self, ctx: dict) -> Vote:
        """LONG-edge vote ONLY. The triple-barrier label is long-geometry
        (TP above, SL below): p <= 0.40 means 'chasing longs has no edge'
        (chop/timeout dominates), it is NOT evidence for shorts - abstaining
        there is the honest reading. SOFT vote unless promoted."""
        p = ctx.get("ml_prob")
        if p is None or (isinstance(p, float) and np.isnan(p)):
            return Vote("ml", 0, 0.0, "ml cold")
        if p >= 0.60:
            return Vote("ml", 1, float(np.clip((p - 0.5) * 2, 0.2, 0.9)),
                        f"p(TP-first) {p:.2f}")
        return Vote("ml", 0, 0.0, f"no long edge {p:.2f}")

    # -------------------------------------------------------------- decide
    def decide(self, ctx: dict) -> Decision:
        d = Decision(regime=str(ctx.get("regime", "RANGE")),
                     session=str(ctx.get("session", "ASIA")),
                     required=self.min_agreement)
        voters = {"trend": self._v_trend, "meanrev": self._v_meanrev,
                  "breakout": self._v_breakout, "psychology": self._v_psychology,
                  "cross_asset": self._v_cross_asset,
                  "news": self._v_news, "ml": self._v_ml}
        net = 0.0
        strength_sum = 0.0
        votes: dict[str, Vote] = {}
        for name, fn in voters.items():          # pass 1: the seven propoents
            v = fn(ctx)
            votes[name] = v
            net += v.direction * v.strength
        pre_lean = 1 if net > 0 else -1 if net < 0 else 0
        votes["micro"] = self._v_micro(ctx, pre_lean)   # pass 2: confirmation

        for name, v in votes.items():
            d.votes[name] = {"dir": v.direction, "strength": round(v.strength, 3),
                             "reason": v.reason}
            d.explain.append(
                f"{name}: {'LONG' if v.direction > 0 else 'SHORT' if v.direction < 0 else 'abstain'}"
                f" ({v.strength:.2f}) {v.reason}")
            net += v.direction * v.strength
            if v.direction != 0:
                strength_sum += v.strength

        side = "LONG" if net > 0 else "SHORT" if net < 0 else None
        if side is None:
            d.explain.append("gate: no directional majority")
            return d
        d.side = side
        want = 1 if side == "LONG" else -1
        d.aligned = sum(1 for v in d.votes.values() if v["dir"] == want)

        # ---- confidence (heuristic, honest): agreement + aligned conviction
        # + psy coverage. The >=4 agreement rule is the win-rate lever; this
        # score only filters degenerate low-conviction alignments.
        psy_conf = 0.5
        row = ctx.get("psy_row")
        if row is not None and hasattr(row, "get"):
            pc = row.get("psy_confidence", np.nan)
            if not pd.isna(pc):
                psy_conf = float(pc)
            fg = row.get("psy_fear_greed", np.nan)
            d.psychology = {
                "fear_greed": round(float(fg), 1) if not pd.isna(fg) else None,
                "regime_tag": str(row.get("psy_regime_tag", "") or ""),
                "capitulation": int(float(row.get("psy_capitulation", 0) or 0)),
                "euphoria": int(float(row.get("psy_euphoria", 0) or 0)),
            }
        avg_strength = (strength_sum / d.aligned) if d.aligned else 0.0
        d.confidence = float(np.clip(
            0.4 * d.aligned / 8.0 + 0.4 * avg_strength + 0.2 * psy_conf, 0.0, 1.0))
        if d.votes["ml"]["dir"] != 0 and d.votes["ml"]["dir"] != want:
            d.confidence -= 0.10

        # ---- hard gates ------------------------------------------------
        if self.psychology is not None and row is not None and hasattr(row, "get"):
            vetoed, why = self.psychology.veto(row, side)
            if vetoed:
                d.vetoes.append(f"psychology:{why}")
        if d.regime == "CRISIS":
            d.vetoes.append("regime:CRISIS no-trade")
        elif d.regime not in self.regime_allow:
            d.vetoes.append(f"regime:{d.regime} not allowed")
        if d.session not in self.sessions_allow:
            d.vetoes.append(f"session:{d.session} not allowed")
        if d.aligned < self.min_agreement:
            d.vetoes.append(f"agreement:{d.aligned}/{self.min_agreement}")
        if d.confidence < self.min_confidence:
            d.vetoes.append(f"confidence:{d.confidence:.2f}<{self.min_confidence}")
        if self.ml_hard and d.votes["ml"]["dir"] != want and d.votes["ml"]["dir"] != 0:
            d.vetoes.append("ml:hard-veto disagreement")

        d.accepted = not d.vetoes
        d.explain.append(f"gate: {'ACCEPT ' + side if d.accepted else 'REJECT'} "
                         f"({d.aligned}/{self.min_agreement} aligned, "
                         f"conf {d.confidence:.2f}, net {net:+.2f})")
        return d
