"""Tests for the HPE psychology layer (core/psychology.py).

Synthetic deterministic bars only — no network, no frozen-file access.
Every scenario is engineered to trip exactly one rule; thresholds carry
margin so the assertions test the RULE, not floating-point luck.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.psychology import PSYCHOLOGY_COLUMNS, PsychologyEngine

REGIMES = {"PANIC", "FEAR", "NEUTRAL", "GREED", "EUPHORIA"}


def _walk_h1(n: int = 600, seed: int = 11) -> pd.DataFrame:
    """Deterministic hourly-UTC OHLCV random walk around 2400."""
    rng = np.random.default_rng(seed)
    closes = 2400.0 + np.cumsum(rng.normal(0.0, 0.9, n))
    vols = 1000.0 + rng.normal(0.0, 40.0, n)
    o = np.concatenate([[closes[0]], closes[:-1]])
    hi = np.maximum(o, closes) + 0.25
    lo = np.minimum(o, closes) - 0.25
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": closes,
                         "volume": vols}, index=idx)


def _extras(index: pd.DatetimeIndex, vix: pd.Series) -> dict[str, pd.Series]:
    """All six external series; only `vix` varies between scenarios."""
    rng = np.random.default_rng(99)
    n = len(index)
    return {
        "vix": vix,
        "dxy": pd.Series(104.0 + rng.normal(0.0, 0.2, n), index=index),
        "us10y": pd.Series(4.2 + rng.normal(0.0, 0.03, n), index=index),
        "gld_vol": pd.Series(50000.0 + rng.normal(0.0, 800.0, n),
                             index=index),
        "funding": pd.Series(0.01 + rng.normal(0.0, 0.002, n), index=index),
        "cot_net": pd.Series(150000.0 + rng.normal(0.0, 2000.0, n),
                             index=index),
    }


def test_composite_bounds_and_confidence():
    h1 = _walk_h1()
    eng = PsychologyEngine()
    out = eng.compute_frame(h1)

    fg = out["psy_fear_greed"]
    assert fg.notna().all()
    assert fg.between(0.0, 100.0).all()
    conf = out["psy_confidence"]
    assert conf.notna().all()
    assert (conf > 0.25).all() and (conf <= 1.0).all()
    # natives only: vix/dxy/funding weights (0.30) are missing → tail < 1
    assert 0.3 < conf.iloc[-1] < 1.0

    n = len(h1)
    t = np.arange(n, dtype=float)
    rng_v = np.random.default_rng(1)
    vix_rise = pd.Series(
        15.0 + 45.0 * t / (n - 1) + rng_v.normal(0.0, 0.1, n), index=h1.index)
    vix_flat = pd.Series(20.0 + rng_v.normal(0.0, 0.1, n), index=h1.index)

    out_rise = eng.compute_frame(h1, extras=_extras(h1.index, vix_rise))
    out_flat = eng.compute_frame(h1, extras=_extras(h1.index, vix_flat))

    # every external present (vix_z needs 20 warmup bars) → full coverage
    assert out_rise["psy_confidence"].iloc[-1] == pytest.approx(1.0)
    assert out_rise["psy_fear_greed"].notna().all()
    assert out_rise["psy_fear_greed"].between(0.0, 100.0).all()
    # rising VIX must drag the composite DOWN versus the flat-VIX run
    rise_tail = out_rise["psy_fear_greed"].tail(300)
    flat_tail = out_flat["psy_fear_greed"].tail(300)
    assert rise_tail.mean() < flat_tail.mean()
    assert (rise_tail.to_numpy() < flat_tail.to_numpy()).mean() > 0.9


def test_capitulation_detection():
    rng = np.random.default_rng(5)
    n = 80
    closes = 2400.0 + 0.05 * np.arange(n) + rng.normal(0.0, 0.05, n)
    vols = 1000.0 + rng.normal(0.0, 15.0, n)
    o = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2025-02-01", periods=n, freq="1h", tz="UTC")
    h1 = pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) + 0.1,
        "low": np.minimum(o, closes) - 0.1,
        "close": closes, "volume": vols,
    }, index=idx)
    # huge down bar: range ~30, volume 5x mean, closing AT the bar low
    i = n - 1
    o_i = h1["open"].iloc[i]
    h1.iloc[i, h1.columns.get_loc("close")] = o_i - 30.0
    h1.iloc[i, h1.columns.get_loc("high")] = o_i + 0.5
    h1.iloc[i, h1.columns.get_loc("low")] = o_i - 30.0
    h1.iloc[i, h1.columns.get_loc("volume")] = 5000.0

    eng = PsychologyEngine()
    out = eng.compute_frame(h1)
    assert out["psy_vol_spike"].iloc[i] == 1.0
    assert out["psy_capitulation"].iloc[i] == 1.0
    assert out["psy_capitulation_score"].iloc[i] > 0.5
    assert out["psy_capitulation"].iloc[: i - 3].max() == 0.0
    assert out["psy_regime_tag"].iloc[i] in {"PANIC", "FEAR"}
    veto_short, why = eng.veto(out.iloc[i], "SHORT")
    assert veto_short is True and "capitulation" in why


def test_euphoria_detection():
    rng = np.random.default_rng(3)
    n = 120
    closes = 2400.0 + rng.normal(0.0, 0.15, n)
    vols = 1000.0 + rng.normal(0.0, 20.0, n)
    o = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2025-03-01", periods=n, freq="1h", tz="UTC")
    h1 = pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) + 0.1,
        "low": np.minimum(o, closes) - 0.1,
        "close": closes, "volume": vols,
    }, index=idx)
    # 12 accelerating up-bars: increments 0.5 .. 6.0 (parabolic advance)
    for j in range(1, 13):
        p = n - 13 + j
        prev_c = h1["close"].iloc[p - 1]
        new_c = prev_c + 0.5 * j
        h1.iloc[p, h1.columns.get_loc("open")] = prev_c
        h1.iloc[p, h1.columns.get_loc("close")] = new_c
        h1.iloc[p, h1.columns.get_loc("high")] = new_c + 0.1
        h1.iloc[p, h1.columns.get_loc("low")] = prev_c - 0.1
        h1.iloc[p, h1.columns.get_loc("volume")] = 1000.0 + 60.0 * j
    # climax last bar: wide range (well past 1.8x ATR) closing at the high
    last = n - 1
    prev_c = h1["close"].iloc[last - 1]
    h1.iloc[last, h1.columns.get_loc("open")] = prev_c
    h1.iloc[last, h1.columns.get_loc("close")] = prev_c + 6.0
    h1.iloc[last, h1.columns.get_loc("high")] = prev_c + 6.3
    h1.iloc[last, h1.columns.get_loc("low")] = prev_c - 0.3
    h1.iloc[last, h1.columns.get_loc("volume")] = 2200.0

    eng = PsychologyEngine()
    out = eng.compute_frame(h1)
    assert out["psy_rsi"].iloc[last] > 80.0
    assert out["psy_range_atr"].iloc[last] >= 1.8
    assert out["psy_euphoria"].iloc[last] == 1.0
    assert 0.0 < out["psy_euphoria_score"].iloc[last] <= 1.0
    assert out["psy_euphoria"].iloc[n - 14] == 0.0  # quiet bar before run
    assert out["psy_regime_tag"].iloc[last] == "EUPHORIA"  # forced by flag
    veto_long, why = eng.veto(out.iloc[last], "LONG")
    assert veto_long is True and "euphoria" in why


def test_veto_table():
    eng = PsychologyEngine()

    def psy_row(**kw: float) -> pd.Series:
        base = {c: np.nan for c in PSYCHOLOGY_COLUMNS
                if c != "psy_regime_tag"}
        base.update(kw)
        return pd.Series(base, dtype=float)

    # fear_greed pinned at 90 → LONG blocked by level, SHORT untouched
    r = psy_row(psy_fear_greed=90.0, psy_euphoria=0.0, psy_capitulation=0.0,
                psy_min_to_event=9999.0, psy_event_relevance=0.0)
    v, why = eng.veto(r, "LONG")
    assert v is True and "euphoria_level" in why
    v, _ = eng.veto(r, "SHORT")
    assert v is False

    # capitulation flag → SHORT blocked, LONG untouched
    r = psy_row(psy_fear_greed=50.0, psy_capitulation=1.0,
                psy_min_to_event=9999.0, psy_event_relevance=0.0)
    v, why = eng.veto(r, "SHORT")
    assert v is True and "capitulation" in why
    v, _ = eng.veto(r, "LONG")
    assert v is False

    # euphoria flag → LONG blocked, SHORT untouched
    r = psy_row(psy_fear_greed=50.0, psy_euphoria=1.0,
                psy_min_to_event=9999.0, psy_event_relevance=0.0)
    v, why = eng.veto(r, "LONG")
    assert v is True and "euphoria" in why
    v, _ = eng.veto(r, "SHORT")
    assert v is False

    # imminent high-impact event at relevance 0.8 → both sides blocked
    r = psy_row(psy_fear_greed=50.0, psy_min_to_event=20.0,
                psy_event_relevance=0.8)
    assert eng.veto(r, "LONG")[0] is True
    assert eng.veto(r, "SHORT")[0] is True
    # same distance but relevance below 0.6 → fail-open, no veto
    r = psy_row(psy_fear_greed=50.0, psy_min_to_event=20.0,
                psy_event_relevance=0.5)
    assert eng.veto(r, "LONG")[0] is False
    assert eng.veto(r, "SHORT")[0] is False

    # all-NaN row → fail-open both sides with the exact unavailability tag
    r = psy_row()
    for side in ("LONG", "SHORT"):
        v, why = eng.veto(r, side)
        assert v is False
        assert why == "psy unavailable"

    with pytest.raises(ValueError):
        eng.veto(r, "SIDEWAYS")


def test_missing_extras_degrade():
    h1 = _walk_h1()
    eng = PsychologyEngine()
    out = eng.compute_frame(h1, extras={})
    out_none = eng.compute_frame(h1)
    pd.testing.assert_frame_equal(out, out_none)  # extras={} ≡ extras=None

    for col in ("psy_vix", "psy_vix_z", "psy_dxy_ret5", "psy_us10y_ret5",
                "psy_gld_flow_z", "psy_funding", "psy_funding_z",
                "psy_cot_net"):
        assert out[col].isna().all()
    assert list(out.columns) == PSYCHOLOGY_COLUMNS
    assert set(out["psy_regime_tag"].unique()).issubset(REGIMES)
    assert out["psy_fear_greed"].notna().all()

    # zero-volume frame: volume z undefined → no spike is ever claimed
    h1_zero = h1.copy()
    h1_zero["volume"] = 0.0
    out_zero = eng.compute_frame(h1_zero)
    assert (out_zero["psy_vol_spike"] == 0.0).all()


def test_calendar_features():
    h1 = _walk_h1(seed=23)
    eng = PsychologyEngine()
    cal = pd.DataFrame({
        "time": [h1.index[300] + pd.Timedelta(minutes=20),
                 h1.index[400] + pd.Timedelta(minutes=200),
                 h1.index[500] + pd.Timedelta(minutes=10)],
        "is_high": [True, True, False],  # third event is low-impact
        "gold_relevance": [0.8, 0.9, 0.99],
    })
    out = eng.compute_frame(h1, calendar=cal)

    # high-impact event 20 min after bar 300 → visible and veto-grade
    assert out["psy_min_to_event"].iloc[300] == pytest.approx(20.0)
    assert out["psy_event_relevance"].iloc[300] == pytest.approx(0.8)
    assert eng.veto(out.iloc[300], "LONG")[0] is True
    assert eng.veto(out.iloc[300], "SHORT")[0] is True

    # event 200 min out: inside the 240-min relevance window, outside veto
    assert out["psy_min_to_event"].iloc[400] == pytest.approx(200.0)
    assert eng.veto(out.iloc[400], "LONG")[0] is False
    assert eng.veto(out.iloc[400], "SHORT")[0] is False

    # low-impact events are invisible to the high-impact lens
    assert out["psy_min_to_event"].iloc[500] == 9999.0
    assert out["psy_event_relevance"].iloc[500] == 0.0

    # no calendar → honest defaults everywhere
    out_nc = eng.compute_frame(h1)
    assert (out_nc["psy_min_to_event"] == 9999.0).all()
    assert (out_nc["psy_event_relevance"] == 0.0).all()


def test_no_lookahead():
    h1 = _walk_h1(seed=42)
    eng = PsychologyEngine()
    full = eng.compute_frame(h1)
    k = 450
    head = eng.compute_frame(h1.iloc[:k])
    part = full.iloc[:k]
    assert len(head) == k
    for col in PSYCHOLOGY_COLUMNS:
        # NaN == NaN compares equal here; exact column-wise identity
        pd.testing.assert_series_equal(part[col], head[col],
                                       check_names=False)
