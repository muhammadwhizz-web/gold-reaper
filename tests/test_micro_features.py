"""HPE Phase 1 :: Micro-Feature Forge — contract tests (hermetic, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.micro_features import (
    MICRO_FEATURE_COLUMNS,
    compute_micro_features,
    validate_no_lookahead,
)

CTX = (2400.0, 2401.5, 2398.5, 2399.5, 100.0)  # small bearish context bar


def _ohlc(rows: list[tuple[float, float, float, float, float]],
          start: str = "2025-01-02") -> pd.DataFrame:
    """Build an hourly OHLCV frame from (open, high, low, close, volume) rows."""
    idx = pd.date_range(start, periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                      index=idx)
    return df.astype(float)


def _random_walk(bars: int, seed: int, start: str = "2025-01-02") -> pd.DataFrame:
    """Deterministic OHLCV random walk (gapped opens, positive ranges)."""
    rng = np.random.default_rng(seed)
    close = 2400.0 + np.cumsum(rng.normal(0.0, 2.0, bars))
    open_ = np.roll(close, 1) + rng.normal(0.0, 0.4, bars)
    open_[0] = 2400.0
    span = np.abs(rng.normal(1.5, 0.5, bars)) + 0.1
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    vol = rng.uniform(500.0, 2000.0, bars)
    idx = pd.date_range(start, periods=bars, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_no_lookahead():
    df = _random_walk(400, seed=7)
    assert validate_no_lookahead(df, 300) is True
    # HARD inline re-check on a handful of representative columns
    full = compute_micro_features(df)
    part = compute_micro_features(df.iloc[:300])
    cols = ["mf_er_20", "mf_streak", "mf_vol_z", "mf_velocity",
            "mf_hurst_100", "mf_engulf_bull", "mf_bars_per_hour",
            "mf_min_of_session", "mf_halflife"]
    a = full[cols].iloc[:300].to_numpy(dtype=float)
    b = part[cols].to_numpy(dtype=float)
    both_nan = np.isnan(a) & np.isnan(b)
    near = np.isclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=False)
    assert bool((near | both_nan).all())
    # frame contract: index preserved, exact ordered float column set
    assert list(full.columns) == MICRO_FEATURE_COLUMNS
    assert full.index.equals(df.index)
    assert (full.dtypes == "float64").all()
    assert full["mf_gap_pct"].iloc[0] == 0.0  # first row gap is 0.0 by contract


def test_anatomy_known_candle():
    # strong bull marubozu: open == low, close == high
    f = compute_micro_features(_ohlc([CTX, CTX, CTX, CTX,
                                      (2400.0, 2410.0, 2400.0, 2410.0, 100.0)]))
    assert f["mf_body_rng"].iloc[4] == pytest.approx(1.0)
    assert f["mf_marubozu"].iloc[4] == 1.0
    assert f["mf_close_pos"].iloc[4] == pytest.approx(1.0)
    assert f["mf_dir"].iloc[4] == 1.0
    assert f["mf_upper_wick"].iloc[4] == 0.0
    assert f["mf_lower_wick"].iloc[4] == 0.0
    assert f["mf_doji"].iloc[4] == 0.0

    # gravestone doji: body at the low, long upper wick
    f = compute_micro_features(_ohlc([CTX, CTX, CTX, CTX,
                                      (2400.0, 2410.0, 2400.0, 2400.0, 100.0)]))
    assert f["mf_doji"].iloc[4] == 1.0
    assert f["mf_doji_gravestone"].iloc[4] == 1.0
    assert f["mf_doji_dragonfly"].iloc[4] == 0.0
    assert f["mf_close_pos"].iloc[4] == pytest.approx(0.0)
    assert f["mf_body_rng"].iloc[4] == 0.0  # flat body -> 0.0 by convention

    # inside bar: fully contained in the previous bar's range
    f = compute_micro_features(_ohlc([CTX, CTX, CTX,
                                      (2395.0, 2410.0, 2390.0, 2405.0, 100.0),
                                      (2398.0, 2405.0, 2396.0, 2400.0, 100.0)]))
    assert f["mf_inside"].iloc[4] == 1.0
    assert f["mf_outside"].iloc[4] == 0.0

    # outside bar: engulfs the previous bar's range
    f = compute_micro_features(_ohlc([CTX, CTX, CTX,
                                      (2398.0, 2405.0, 2396.0, 2400.0, 100.0),
                                      (2394.0, 2412.0, 2390.0, 2408.0, 100.0)]))
    assert f["mf_outside"].iloc[4] == 1.0
    assert f["mf_inside"].iloc[4] == 0.0

    # gap up over the previous close
    f = compute_micro_features(_ohlc([CTX, CTX, CTX, CTX,
                                      (2410.0, 2412.0, 2408.0, 2411.0, 100.0)]))
    expected = (2410.0 - 2399.5) / 2399.5 * 100.0
    assert f["mf_gap_pct"].iloc[4] == pytest.approx(expected)


def test_patterns_fire():
    # bullish engulfing at bar 5 (opposite colors + body engulfs prev body)
    f = compute_micro_features(_ohlc([CTX, CTX, CTX, CTX,
                                      (2405.0, 2406.0, 2394.0, 2395.0, 100.0),
                                      (2394.0, 2407.0, 2393.0, 2406.0, 100.0)]))
    assert f["mf_engulf_bull"].iloc[5] == 1.0
    assert (f["mf_engulf_bull"].iloc[:5] == 0.0).all()
    assert f["mf_engulf_bear"].iloc[5] == 0.0
    assert f["mf_harami_bull"].iloc[5] == 0.0  # body engulfs, not inside

    # three soldiers: 3 bullish bodies with strictly rising closes
    f = compute_micro_features(_ohlc([CTX, CTX, CTX,
                                      (2400.0, 2402.5, 2399.5, 2402.0, 100.0),
                                      (2402.5, 2405.5, 2402.0, 2405.0, 100.0),
                                      (2405.5, 2408.5, 2405.0, 2408.0, 100.0)]))
    assert f["mf_three_soldiers"].iloc[5] == 1.0
    assert (f["mf_three_soldiers"].iloc[:5] == 0.0).all()

    # three crows: mirror
    f = compute_micro_features(_ohlc([CTX, CTX, CTX,
                                      (2400.0, 2400.5, 2397.5, 2398.0, 100.0),
                                      (2398.0, 2398.5, 2395.0, 2395.0, 100.0),
                                      (2395.0, 2395.5, 2392.0, 2392.0, 100.0)]))
    assert f["mf_three_crows"].iloc[5] == 1.0

    # hammer: bull pin after a 3-bar decline
    f = compute_micro_features(_ohlc([
        (2421.0, 2421.5, 2419.5, 2420.0, 100.0),
        (2420.0, 2420.5, 2414.5, 2415.0, 100.0),
        (2415.0, 2415.5, 2409.5, 2410.0, 100.0),
        (2410.0, 2410.5, 2404.5, 2405.0, 100.0),
        (2405.0, 2407.5, 2395.0, 2406.5, 100.0)]))
    assert f["mf_pin_bull"].iloc[4] == 1.0
    assert f["mf_hammer"].iloc[4] == 1.0
    assert f["mf_shooting_star"].iloc[4] == 0.0

    # shooting star: bear pin after a 3-bar advance
    f = compute_micro_features(_ohlc([
        (2399.5, 2400.5, 2399.0, 2400.0, 100.0),
        (2400.0, 2405.5, 2399.5, 2405.0, 100.0),
        (2405.0, 2410.5, 2404.5, 2410.0, 100.0),
        (2410.0, 2415.5, 2409.5, 2415.0, 100.0),
        (2416.0, 2426.0, 2415.0, 2418.0, 100.0)]))
    assert f["mf_pin_bear"].iloc[4] == 1.0
    assert f["mf_shooting_star"].iloc[4] == 1.0
    assert f["mf_hammer"].iloc[4] == 0.0


def test_streak_and_alt():
    rows = []
    c = 2399.0
    for _ in range(5):
        o = c
        c = c + 1.0
        rows.append((o, c + 0.4, o - 0.4, c, 100.0))
    f = compute_micro_features(_ohlc(rows))
    assert f["mf_streak"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]

    rows = []
    for i in range(11):
        o = 2400.0 + i * 0.5
        c = o + (1.0 if i % 2 == 0 else -1.0)
        rows.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c, 100.0))
    f = compute_micro_features(_ohlc(rows))
    assert f["mf_alt_count"].iloc[10] == 10.0  # every one of 10 transitions flips
    assert f["mf_streak"].iloc[10] == 1.0      # never two same-direction bars
    assert f["mf_alt_count"].iloc[:9].isna().all()  # 10-bar rolling warmup


def test_er_bounds():
    rows = []
    for i in range(21):
        o = 2399.5 + i
        c = 2400.5 + i
        rows.append((o, c + 0.4, o - 0.4, c, 100.0))
    f = compute_micro_features(_ohlc(rows))
    assert f["mf_er_20"].iloc[20] == pytest.approx(1.0, abs=1e-9)

    rows = [(2400.0, 2400.4, 2399.6, 2400.0, 100.0)]
    c = 2400.0
    for i in range(1, 21):
        o = c
        c = c + (1.0 if i % 2 == 1 else -1.0)
        rows.append((o, max(o, c) + 0.4, min(o, c) - 0.4, c, 100.0))
    f = compute_micro_features(_ohlc(rows))
    assert f["mf_er_20"].iloc[20] == pytest.approx(0.0, abs=1e-9)  # net 0 move


def test_warmup_nan():
    df = _random_walk(120, seed=11)
    f = compute_micro_features(df)
    assert pd.isna(f["mf_er_50"].iloc[40])
    assert np.isfinite(f["mf_er_50"].iloc[60])
    assert pd.isna(f["mf_er_20"].iloc[19])
    assert np.isfinite(f["mf_er_20"].iloc[20])
    rng_atr = f["mf_range_atr"]
    assert rng_atr.iloc[:14].isna().all()          # exactly the ATR warmup
    assert np.isfinite(rng_atr.iloc[14:].to_numpy()).all()


def test_lower_tf_enrichment():
    idx = pd.date_range("2025-01-06 00:00", periods=60, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": 2400.0, "high": 2401.0, "low": 2399.0,
                       "close": 2400.5, "volume": 1000.0}, index=idx)
    vol_by_hour = {2: np.arange(1, 61) * 1.0,
                   3: np.arange(1, 61) * 3.0 + 7.0,
                   4: np.arange(1, 61) * 0.5 + 100.0}
    lts = []
    for hour, vol in vol_by_hour.items():
        t_idx = pd.date_range(f"2025-01-06 {hour - 1:02d}:01", periods=60,
                              freq="1min", tz="UTC")
        lts.append(pd.DataFrame({"open": 2400.0, "high": 2400.6,
                                 "low": 2399.8, "close": 2400.2,
                                 "volume": vol}, index=t_idx))
    lt = pd.concat(lts).sort_index()
    f = compute_micro_features(df, lower_tf=lt)

    tp = f["mf_ticks_per_bar"]
    assert (tp.iloc[[2, 3, 4]] == 60.0).all()
    assert tp.drop(index=idx[[2, 3, 4]]).isna().all()  # uncovered -> NaN
    vc = f["mf_vol_conc"]
    for row, vol in zip((2, 3, 4), vol_by_hour.values()):
        assert vc.iloc[row] == pytest.approx(vol.max() / vol.sum())
    assert vc.drop(index=idx[[2, 3, 4]]).isna().all()

    # boundary + no future leakage: a 1m bar stamped 01:00 belongs to row 1
    # (its own close hour), row 0's window stays empty, row 2 keeps ONLY
    # hour-2 volumes even though hour-3 bars already exist upstream.
    bnd = pd.DataFrame({"open": 2400.0, "high": 2400.6, "low": 2399.8,
                        "close": 2400.2, "volume": 999.0},
                       index=[pd.Timestamp("2025-01-06 01:00", tz="UTC")])
    f2 = compute_micro_features(df, lower_tf=pd.concat([lt, bnd]).sort_index())
    assert f2["mf_ticks_per_bar"].iloc[1] == 1.0
    assert f2["mf_vol_conc"].iloc[1] == pytest.approx(1.0)
    assert pd.isna(f2["mf_ticks_per_bar"].iloc[0])

    # no lower_tf -> both columns NaN, never a crash
    f3 = compute_micro_features(df)
    assert f3["mf_ticks_per_bar"].isna().all()
    assert f3["mf_vol_conc"].isna().all()


def test_zero_volume_and_flat():
    idx = pd.date_range("2025-01-02", periods=100, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": 2400.0, "high": 2400.0, "low": 2400.0,
                       "close": 2400.0, "volume": 0.0}, index=idx)
    f = compute_micro_features(df)  # must not raise
    assert list(f.columns) == MICRO_FEATURE_COLUMNS
    for col in f.columns:  # every non-NaN value finite: no inf anywhere
        vals = f[col].dropna().to_numpy(dtype=float)
        assert np.isfinite(vals).all(), f"{col} has non-finite values"
    assert (f["mf_bars_per_hour"] == 1.0).all()
    assert (f["mf_vol_z"].dropna() == 0.0).all()   # zero-volume std guard
    assert (f["mf_fd_50"].dropna() == 1.5).all()   # degenerate span guard
    assert (f["mf_hurst_100"].dropna() == 0.5).all()  # zero-std guard
    assert (f["mf_dir"] == 0.0).all()
    assert (f["mf_streak"] == 0.0).all()
