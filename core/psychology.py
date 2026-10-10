"""
GOLD REAPER HPE :: Psychology Layer — fear, greed, capitulation, euphoria
==========================================================================
Phase 2 of the High-Probability Edge engine. Quantifies market psychology
per bar from price/volume natives (always available) plus optional external
series (VIX, DXY, US10Y, GLD flows, Bitget funding, COT) that degrade
gracefully to neutral when absent. Produces a 0-100 fear/greed composite,
regime tags, and hard vetoes consumed by the HPE ensemble.
Honesty rule: confidence reflects data coverage; absent data never fakes a signal.

Exact rules (implemented verbatim — simple, documented, auditable):

  mom_z      = (close - close.shift(20)) / (close.diff().rolling(20).std()
               * sqrt(20)), clipped to ±6 (the clipped value is used
               everywhere downstream). vol_z and external z-scores are
               20-bar rolling z-scores; zero dispersion → NaN → the feature
               is UNAVAILABLE, never faked.
  capitulation: trigger bar t requires vol_spike[t] == 1 AND
               range_atr[t] >= cfg.capitulation_range_atr AND close[t] <
               open[t] AND close_pos[t] <= 0.25, where close_pos =
               (close - low) / (high - low). score[t] = min(1, vol_z[t]/4).
               REVERSAL HOLD: when close[t+1] > close[t] (the bar after a
               trigger closes up), score[t+1] and score[t+2] are forced to
               1.0 (two bars after a trigger that reverses). All other
               bars score 0.0. psy_capitulation = 1 wherever score > 0.
  euphoria:   mom_z >= cfg.euphoria_mom_z AND rsi14 >= cfg.euphoria_rsi AND
               range_atr >= 1.8 → score = min(1, mom_z / 5), else 0.
               psy_euphoria = 1 wherever score > 0.
  denial:     atr14 pct-rank over the trailing 500 bars (min_periods 100)
               < 0.35 AND adx14 < adx14.shift(10) AND vol_z < 0 → 1 else 0.
  crowding:   adx14 > cfg.crowding_adx AND |mom_z| > 2 AND vol_z > 0.5 →
               min(1, (adx14 - 20) / 30), magnitude only. Crowding is a
               confidence discount for the ensemble — it never moves the
               composite level.
  composite:  weighted mean over AVAILABLE components only (weights:
               momentum .25 / rsi .15 / vol .15 / streak .15 / vix .10 /
               dxy .10 / funding .10, total 1.0). psy_confidence =
               covered_weight / total_weight. Result clipped to 0..100.
  regime tag: PANIC ≤15 | FEAR ≤40 | NEUTRAL ≤60 | GREED ≤85 | EUPHORIA >85.
               A capitulation flag forces the tag ≥ one step down toward
               PANIC (floored at PANIC); a euphoria flag forces EUPHORIA
               (applied last, overrides the capitulation step). A NaN
               composite maps to NEUTRAL, so the tag is always one of five.
  veto:       LONG blocked by the euphoria flag, fear_greed >= 85 (cfg), or
               a high-impact event within 30 min (cfg) at relevance >= 0.6.
               SHORT blocked by the capitulation flag, fear_greed <= 15
               (cfg), or the same event rule. NaN components fail open one
               by one; NaN fear_greed → (False, "psy unavailable").
  calendar:   is_high events only; minutes to the NEXT event via
               searchsorted (mirrors features/build_features.news_features);
               relevance is recorded while that event is ≤ 240 min out.
               psy_news_sent / psy_news_conf stay 0.0 until a news table is
               wired in a later phase.

Extras keys: "vix", "dxy", "us10y", "gld_vol", "funding", "cot_net" — any
subset, reindexed to the h1 index; anything missing becomes a NaN column.
cot_net is accepted and passed through as psy_cot_net but carries no
composite weight (the spec defines none); unknown extras keys are ignored.
Bars are assumed chronological on a DatetimeIndex; the engine raises on a
non-DatetimeIndex instead of guessing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators import adx, atr, rsi

__all__ = ["PSYCHOLOGY_COLUMNS", "PsychologyEngine"]

PSYCHOLOGY_COLUMNS: list[str] = [
    "psy_rsi", "psy_mom_z", "psy_vol_spike", "psy_range_atr",
    "psy_capitulation_score", "psy_capitulation",
    "psy_euphoria_score", "psy_euphoria", "psy_denial",
    "psy_crowding_score", "psy_streak_extreme", "psy_streak_signed",
    "psy_vix", "psy_vix_z", "psy_dxy_ret5", "psy_us10y_ret5",
    "psy_gld_flow_z", "psy_funding", "psy_funding_z", "psy_cot_net",
    "psy_news_sent", "psy_news_conf", "psy_min_to_event",
    "psy_event_relevance", "psy_fear_greed", "psy_confidence",
    "psy_regime_tag",
]

_FEAR_GREED_WEIGHTS: dict[str, float] = {
    "momentum": 0.25, "rsi": 0.15, "vol": 0.15, "streak": 0.15,
    "vix": 0.10, "dxy": 0.10, "funding": 0.10,
}

_REGIME_TAGS: list[str] = ["PANIC", "FEAR", "NEUTRAL", "GREED", "EUPHORIA"]
_EVENT_RELEVANCE_MIN = 0.6
_EVENT_WINDOW_MIN = 240.0
_NO_EVENT_MIN = 9999.0

_DEFAULTS: dict[str, float] = {
    "vol_spike_z": 2.0,
    "capitulation_range_atr": 2.0,
    "euphoria_mom_z": 3.0,
    "euphoria_rsi": 80.0,
    "crowding_adx": 25.0,
    "panic_level": 15.0,
    "euphoria_level": 85.0,
    "veto_event_minutes": 30.0,
}


def _z20(series: pd.Series) -> pd.Series:
    """20-bar rolling z-score; zero dispersion → NaN (unavailable)."""
    sd = series.rolling(20).std()
    return (series - series.rolling(20).mean()) / sd.replace(0.0, np.nan)


def _atr_pct_rank(atr14: pd.Series) -> pd.Series:
    """Pct-rank of the current ATR inside the trailing 500 bars."""
    return atr14.rolling(500, min_periods=100).rank(pct=True)


def _streak_signed(close: pd.Series) -> pd.Series:
    """Consecutive same-direction closes: +n up-run, -n down-run, 0 flat."""
    direction = np.sign(close.diff()).fillna(0.0)
    out = np.zeros(len(direction), dtype=float)
    cur = 0.0
    for i, d in enumerate(direction.to_numpy()):
        if d == 0:
            cur = 0.0
        elif d > 0:
            cur = cur + 1.0 if cur > 0 else 1.0
        else:
            cur = cur - 1.0 if cur < 0 else -1.0
        out[i] = cur
    return pd.Series(out, index=close.index)


def _as_utc_naive(idx_like: pd.Series | pd.Index) -> pd.DatetimeIndex:
    """Normalize datetimes to tz-naive UTC for numpy searchsorted."""
    di = pd.DatetimeIndex(idx_like)
    if di.tz is not None:
        di = di.tz_convert("UTC").tz_localize(None)
    return di


def _event_features(
    calendar: pd.DataFrame | None, index: pd.DatetimeIndex
) -> tuple[pd.Series, pd.Series]:
    """Minutes to the NEXT is_high event + its relevance.

    Only future events are matched (searchsorted, side='left') — the
    calendar itself is legitimately forward-known, so this is not
    lookahead. Mirrors features/build_features.news_features(): relevance
    is kept while the next event is within 240 minutes. Any parsing
    failure degrades to the honest default (no event intel)."""
    mins = pd.Series(_NO_EVENT_MIN, index=index)
    relev = pd.Series(0.0, index=index)
    if calendar is None or len(calendar) == 0:
        return mins, relev
    try:
        cal = (calendar.reset_index() if "time" not in calendar.columns
               else calendar)
        if len(cal) == 0 or "is_high" not in cal.columns:
            return mins, relev
        hi = cal[cal["is_high"].fillna(False).astype(bool)]
        if hi.empty:
            return mins, relev
        tvals = _as_utc_naive(hi["time"]).to_numpy(dtype="datetime64[ns]")
        hi = hi.assign(_t=tvals).dropna(subset=["_t"]).sort_values("_t")
        times = hi["_t"].to_numpy(dtype="datetime64[ns]")
        rel = (hi["gold_relevance"].astype(float).fillna(0.0).to_numpy()
               if "gold_relevance" in hi.columns else np.zeros(len(hi)))
        idx64 = _as_utc_naive(index).to_numpy(dtype="datetime64[ns]")
        mins_v = np.full(len(index), _NO_EVENT_MIN)
        relev_v = np.zeros(len(index))
        positions = np.searchsorted(times, idx64, side="left")
        for i, (pos, t) in enumerate(zip(positions, idx64)):
            if pos < len(times):
                dt = float((times[pos] - t) / np.timedelta64(1, "m"))
                mins_v[i] = max(dt, 0.0)
                if 0.0 <= dt <= _EVENT_WINDOW_MIN:
                    relev_v[i] = float(rel[pos])
    except Exception:
        return mins, relev
    return pd.Series(mins_v, index=index), pd.Series(relev_v, index=index)


def _num(row: pd.Series, col: str) -> float:
    """Robust float read from a psy row; anything odd → NaN (fail-open)."""
    try:
        raw = row[col]
    except (KeyError, IndexError):
        return float("nan")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


class PsychologyEngine:
    """HPE Phase 2 — per-bar market psychology. See module docstring."""

    name = "HPE-PSY"

    def __init__(self, cfg: dict | None = None) -> None:
        merged = dict(_DEFAULTS)
        if cfg:
            merged.update(
                {k: float(v) for k, v in cfg.items() if k in _DEFAULTS})
        self.cfg: dict[str, float] = merged

    def compute_frame(
        self,
        h1: pd.DataFrame,
        extras: dict[str, pd.Series] | None = None,
        calendar: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute every PSYCHOLOGY_COLUMNS row over h1 (no lookahead)."""
        if not isinstance(h1.index, pd.DatetimeIndex):
            raise ValueError("PsychologyEngine needs a DatetimeIndex h1 frame")
        idx = h1.index
        n = len(idx)
        close = h1["close"].astype(float)
        open_ = h1["open"].astype(float) if "open" in h1.columns else close
        high = h1["high"].astype(float) if "high" in h1.columns else close
        low = h1["low"].astype(float) if "low" in h1.columns else close
        vol = (h1["volume"].astype(float) if "volume" in h1.columns
               else pd.Series(0.0, index=idx))
        ohlc = pd.DataFrame({"high": high, "low": low, "close": close},
                            index=idx)

        c = self.cfg
        rsi14 = rsi(close, 14)
        atr14 = atr(ohlc, 14)
        adx14 = adx(ohlc, 14)
        atr_rank = _atr_pct_rank(atr14)

        mom_z = ((close - close.shift(20))
                 / (close.diff().rolling(20).std() * float(np.sqrt(20.0)))
                 ).clip(-6.0, 6.0)
        vol_z = _z20(vol)
        vol_spike = (vol_z > c["vol_spike_z"]).astype(float)
        range_atr = (high - low) / atr14.replace(0.0, np.nan)
        close_pos = (close - low) / (high - low).replace(0.0, np.nan)

        # -- capitulation: vol spike + wide DOWN bar closing at the low ----
        trig = ((vol_spike > 0)
                & (range_atr >= c["capitulation_range_atr"])
                & (close < open_)
                & (close_pos <= 0.25))
        cap_score = pd.Series(
            np.where(trig.to_numpy(),
                     (vol_z / 4.0).clip(0.0, 1.0).to_numpy(), 0.0),
            index=idx,
        )
        rev = trig.shift(1, fill_value=False) & (close > close.shift(1))
        hold = rev | rev.shift(1, fill_value=False)  # bars t+1 and t+2
        cap_score = cap_score.where(~hold, 1.0)
        cap_flag = (cap_score > 0).astype(float)

        # -- euphoria: parabolic advance climaxing ------------------------
        eup_mask = ((mom_z >= c["euphoria_mom_z"])
                    & (rsi14 >= c["euphoria_rsi"])
                    & (range_atr >= 1.8))
        eup_score = pd.Series(
            np.where(eup_mask.to_numpy(),
                     (mom_z / 5.0).clip(0.0, 1.0).to_numpy(), 0.0),
            index=idx,
        )
        eup_flag = (eup_score > 0).astype(float)

        # -- denial: post-shock consolidation ------------------------------
        denial = ((atr_rank < 0.35)
                  & (adx14 < adx14.shift(10))
                  & (vol_z < 0)).astype(float)

        # -- crowding: trend-chase, magnitude only -------------------------
        crowd_mask = ((adx14 > c["crowding_adx"])
                      & (mom_z.abs() > 2.0)
                      & (vol_z > 0.5))
        crowd = pd.Series(
            np.where(crowd_mask.to_numpy(),
                     ((adx14 - 20.0) / 30.0).clip(0.0, 1.0).to_numpy(), 0.0),
            index=idx,
        )

        streak_signed = _streak_signed(close)
        streak_extreme = (streak_signed.abs() / 10.0).clip(0.0, 1.0)

        # -- external passthroughs (absent → NaN, never faked) -------------
        ex = extras or {}

        def ext(key: str) -> pd.Series:
            s = ex.get(key)
            if s is None:
                return pd.Series(np.nan, index=idx, dtype=float)
            try:
                return pd.Series(s).astype(float).reindex(idx)
            except Exception:
                return pd.Series(np.nan, index=idx, dtype=float)

        vix = ext("vix")
        vix_z = _z20(vix)
        dxy_ret5 = ext("dxy").pct_change(5, fill_method=None)
        us10y_ret5 = ext("us10y").pct_change(5, fill_method=None)
        gld_flow_z = _z20(ext("gld_vol"))
        funding = ext("funding")
        funding_z = _z20(funding)
        cot_net = ext("cot_net")

        # -- news/event (news table not wired yet → sent/conf stay 0.0) ----
        news_sent = pd.Series(0.0, index=idx)
        news_conf = pd.Series(0.0, index=idx)
        min_to_event, event_relev = _event_features(calendar, idx)

        # -- fear/greed composite over AVAILABLE components ----------------
        mom_c = 50.0 + 50.0 * np.tanh(mom_z / 3.0)
        streak_c = 50.0 + 50.0 * np.tanh(streak_signed / 5.0)
        rsi_c = rsi14.clip(0.0, 100.0)
        vol_c = 100.0 - 100.0 * atr_rank.clip(0.0, 1.0)  # high vol = fear
        vix_c = 50.0 - 12.0 * vix_z
        dxy_c = 50.0 - 40.0 * (dxy_ret5 / 1.5).clip(-1.0, 1.0)
        fund_c = 50.0 + 25.0 * funding_z.clip(-2.0, 2.0)

        comps = (("momentum", mom_c), ("rsi", rsi_c), ("vol", vol_c),
                 ("streak", streak_c), ("vix", vix_c), ("dxy", dxy_c),
                 ("funding", fund_c))
        cov = np.zeros(n, dtype=float)
        acc = np.zeros(n, dtype=float)
        for key, comp in comps:
            arr = np.asarray(comp, dtype=float)
            ok = np.isfinite(arr)
            w = _FEAR_GREED_WEIGHTS[key]
            cov += w * ok
            acc += w * np.where(ok, arr, 0.0)
        total_w = float(sum(_FEAR_GREED_WEIGHTS.values()))
        fg_arr = np.divide(acc, cov, out=np.full(n, np.nan), where=cov > 0)
        fear_greed = pd.Series(np.clip(fg_arr, 0.0, 100.0), index=idx)
        confidence = pd.Series(np.clip(cov / total_w, 0.0, 1.0), index=idx)

        # -- regime tag (capitulation steps down, euphoria forces up) ------
        tags: list[str] = []
        for v, cap, eup in zip(fear_greed.to_numpy(), cap_flag.to_numpy(),
                               eup_flag.to_numpy()):
            if not np.isfinite(v):
                i = 2
            elif v <= 15.0:
                i = 0
            elif v <= 40.0:
                i = 1
            elif v <= 60.0:
                i = 2
            elif v <= 85.0:
                i = 3
            else:
                i = 4
            if cap > 0 and i > 0:
                i -= 1  # >= one step down toward PANIC, floored at PANIC
            if eup > 0:
                i = 4  # euphoria flag forces EUPHORIA (applied last)
            tags.append(_REGIME_TAGS[i])
        regime = pd.Series(tags, index=idx, dtype=object)

        frame = pd.DataFrame(
            {
                "psy_rsi": rsi14.astype(float),
                "psy_mom_z": mom_z.astype(float),
                "psy_vol_spike": vol_spike.astype(float),
                "psy_range_atr": range_atr.astype(float),
                "psy_capitulation_score": cap_score.astype(float),
                "psy_capitulation": cap_flag,
                "psy_euphoria_score": eup_score.astype(float),
                "psy_euphoria": eup_flag,
                "psy_denial": denial,
                "psy_crowding_score": crowd.astype(float),
                "psy_streak_extreme": streak_extreme.astype(float),
                "psy_streak_signed": streak_signed.astype(float),
                "psy_vix": vix,
                "psy_vix_z": vix_z.astype(float),
                "psy_dxy_ret5": dxy_ret5.astype(float),
                "psy_us10y_ret5": us10y_ret5.astype(float),
                "psy_gld_flow_z": gld_flow_z.astype(float),
                "psy_funding": funding,
                "psy_funding_z": funding_z.astype(float),
                "psy_cot_net": cot_net,
                "psy_news_sent": news_sent,
                "psy_news_conf": news_conf,
                "psy_min_to_event": min_to_event.astype(float),
                "psy_event_relevance": event_relev.astype(float),
                "psy_fear_greed": fear_greed.astype(float),
                "psy_confidence": confidence.astype(float),
                "psy_regime_tag": regime,
            },
            index=idx,
        )
        return frame[PSYCHOLOGY_COLUMNS]

    def veto(self, psy_row: pd.Series, side: str) -> tuple[bool, str]:
        """Hard psychology veto for one bar.

        NaN/missing components fail open one by one; a NaN fear_greed
        marks the whole row unavailable → (False, "psy unavailable").
        Unknown side raises (fail loudly, never silently trade)."""
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be 'LONG' or 'SHORT', got {side!r}")
        fg = _num(psy_row, "psy_fear_greed")
        if not np.isfinite(fg):
            return False, "psy unavailable"
        c = self.cfg
        ev_min = _num(psy_row, "psy_min_to_event")
        ev_rel = _num(psy_row, "psy_event_relevance")
        event_hit = (bool(np.isfinite(ev_min)) and bool(np.isfinite(ev_rel))
                     and ev_min <= c["veto_event_minutes"]
                     and ev_rel >= _EVENT_RELEVANCE_MIN)
        if side == "LONG":
            eup = _num(psy_row, "psy_euphoria")
            if bool(np.isfinite(eup)) and eup == 1.0:
                return True, "veto LONG: euphoria flag (psy_euphoria == 1)"
            if fg >= c["euphoria_level"]:
                return True, (f"veto LONG: fear_greed {fg:.1f} >= "
                              f"euphoria_level {c['euphoria_level']:.1f}")
            if event_hit:
                return True, (f"veto LONG: high-impact event in {ev_min:.0f}"
                              f" min (relevance {ev_rel:.2f})")
        else:
            cap = _num(psy_row, "psy_capitulation")
            if bool(np.isfinite(cap)) and cap == 1.0:
                return True, \
                    "veto SHORT: capitulation flag (psy_capitulation == 1)"
            if fg <= c["panic_level"]:
                return True, (f"veto SHORT: fear_greed {fg:.1f} <= "
                              f"panic_level {c['panic_level']:.1f}")
            if event_hit:
                return True, (f"veto SHORT: high-impact event in {ev_min:.0f}"
                              f" min (relevance {ev_rel:.2f})")
        return False, ""
