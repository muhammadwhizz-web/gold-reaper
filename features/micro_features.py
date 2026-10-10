"""
GOLD REAPER HPE :: Micro-Feature Forge — every bar, every candle, every minute
==============================================================================
Phase 1 of the High-Probability Edge engine. Computes per-bar microstructure
features on any OHLCV frame (1s/1m/5m/15m/1h/4h/1d). STRICT no-lookahead:
row t uses only data up to and including t. All columns prefixed mf_.

Contract conventions (downstream modules depend on every line here):
  - Output: same DatetimeIndex as the input, float-only columns, ordered
    exactly as MICRO_FEATURE_COLUMNS. The frame index must be
    chronologically sorted (standard for bar data).
  - Flags are strictly 0.0/1.0. A flag whose required history does not
    exist yet (e.g. needs a previous bar on row 0) is 0.0: a pattern
    cannot have formed without its history.
  - Continuous rolling features are NaN while their window is not full.
  - ATR is imported read-only from core.indicators and masked NaN for the
    first `atr_period` rows (warmup); every ATR-derived column inherits
    that mask.
  - Degenerate bars (high == low) never produce inf: body_rng 0.0,
    close_pos 0.5, wick fractions 0.0 (0/0 resolved by convention).
  - Bar direction is sign(close - open) == mf_dir. Streaks and flips use
    it; a doji (dir 0) resets the streak to 0, and any signed-direction
    change (including through 0) counts as a flip for mf_alt_count.
  - mf_halflife: 999.0 sentinel when AR(1) phi is outside (0, 1) — no
    measurable mean-reversion half-life; NaN during warmup or when the
    return variance is zero.
  - lower_tf enrichment maps each df bar to the lower-tf bars stamped in
    (t_prev, t] (close-stamp convention). For a 1h df that is exactly the
    hour bucket, and data from t or beyond can never enter row t. Rows
    without lower-tf coverage stay NaN; covered rows with zero total
    lower-tf volume get mf_vol_conc = NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators import atr

__all__ = ["MICRO_FEATURE_COLUMNS", "compute_micro_features", "validate_no_lookahead"]

MICRO_FEATURE_COLUMNS: list[str] = [
    # A. bar anatomy
    "mf_body_rng", "mf_body_pct", "mf_upper_wick", "mf_lower_wick",
    "mf_close_pos", "mf_range_atr", "mf_dir", "mf_gap_pct",
    # B. bar sequencing / pattern flags
    "mf_streak", "mf_alt_count", "mf_inside", "mf_outside",
    "mf_engulf_bull", "mf_engulf_bear", "mf_harami_bull", "mf_harami_bear",
    "mf_three_soldiers", "mf_three_crows", "mf_doji", "mf_doji_dragonfly",
    "mf_doji_gravestone", "mf_marubozu", "mf_pin_bull", "mf_pin_bear",
    "mf_hammer", "mf_shooting_star",
    # C. bar-rate / activity
    "mf_bars_per_hour", "mf_vol_z", "mf_move_per_bar", "mf_accel",
    # D. bar-movement dynamics
    "mf_velocity", "mf_accel2", "mf_jerk", "mf_mom_decay", "mf_ar1_phi",
    "mf_halflife",
    # E. micro-timing
    "mf_hour", "mf_min_bucket", "mf_min_of_session", "mf_dow",
    # F. path metrics (backward-looking only)
    "mf_er_20", "mf_er_50", "mf_fd_50", "mf_hurst_100",
    "mf_path_mfe20", "mf_path_mae20",
    # G. lower-timeframe enrichment
    "mf_ticks_per_bar", "mf_vol_conc",
]

_HOUR_NS = 3_600_000_000  # one hour in epoch nanoseconds


def _utc_ns(index: pd.DatetimeIndex) -> np.ndarray:
    """int64 epoch-ns view of a DatetimeIndex; naive stamps treated as UTC."""
    idx = index.tz_localize("UTC") if index.tz is None else index
    return np.asarray(idx.asi8, dtype=np.int64)


def _lower_tf_enrichment(
    idx: pd.DatetimeIndex, lower_tf: pd.DataFrame | None
) -> tuple[pd.Series, pd.Series]:
    """Map lower-tf bars onto df rows through strictly backward windows.

    A lower-tf bar stamped x belongs to df row t iff t_prev < x <= t
    (t_prev = previous df timestamp; row 0 uses (t0 - median_bar, t0]).
    Bars stamped after the last df bar belong to no row -> zero leakage.
    Returns (ticks_per_bar, vol_conc), NaN where uncovered / zero volume.
    """
    nan = pd.Series(np.nan, index=idx, dtype=float)
    if lower_tf is None or len(lower_tf) == 0:
        return nan, nan.copy()
    if not isinstance(lower_tf.index, pd.DatetimeIndex):
        raise TypeError("lower_tf index must be a DatetimeIndex")
    lt = lower_tf.sort_index()
    t_ns = _utc_ns(idx)
    x_ns = _utc_ns(lt.index)
    n = len(t_ns)
    diffs = np.diff(t_ns)
    bar0 = int(np.median(diffs)) if diffs.size else _HOUR_NS
    if bar0 <= 0:
        bar0 = _HOUR_NS
    pos = np.searchsorted(t_ns, x_ns, side="left")  # first df row with t >= x
    keep = pos < n
    if n:
        keep &= (pos > 0) | (x_ns > t_ns[0] - bar0)
    rows = pos[keep]
    if rows.size == 0:
        return nan, nan.copy()
    counts = np.bincount(rows, minlength=n).astype(float)
    ticks = pd.Series(np.where(counts > 0, counts, np.nan), index=idx, dtype=float)
    conc = nan.copy()
    if "volume" in lt.columns:
        vol = lt["volume"].astype(float).to_numpy()[keep]
        grouped = pd.Series(vol).groupby(rows)
        v_max = grouped.max().reindex(range(n)).to_numpy(dtype=float)
        v_sum = grouped.sum().reindex(range(n)).to_numpy(dtype=float)
        denom = np.where(v_sum > 0, v_sum, 1.0)
        conc = pd.Series(np.where(v_sum > 0, v_max / denom, np.nan),
                         index=idx, dtype=float)
    return ticks, conc


def compute_micro_features(
    df: pd.DataFrame,
    atr_period: int = 14,
    lower_tf: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute all mf_* micro features for one OHLCV frame.

    df needs a sorted DatetimeIndex and open/high/low/close (+ volume,
    zeros tolerated). lower_tf (optional, e.g. 1m bars) adds per-bar tick
    count and volume concentration. Warmup rows are NaN where a rolling
    window is not yet full; flags are 0.0 until their history exists.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("df index must be a DatetimeIndex")
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise KeyError(f"df missing required OHLC columns: {missing}")
    idx = df.index
    n = len(df)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    low = df["low"].astype(float)
    c = df["close"].astype(float)
    v = (df["volume"].astype(float) if "volume" in df.columns
         else pd.Series(0.0, index=idx))

    out: dict[str, pd.Series] = {}

    # ---- shared primitives -------------------------------------------------
    body = c - o
    abs_body = body.abs()
    rng = h - low
    rng_pos = rng > 0
    flat = rng == 0
    rng_safe = rng.where(rng_pos)  # NaN on flat bars -> guarded ratios
    open_safe = o.where(o != 0)  # body_pct guard: never inf
    prev_c = c.shift(1)
    prev_c_safe = prev_c.where(prev_c != 0)
    oc_max = pd.concat([o, c], axis=1).max(axis=1)
    oc_min = pd.concat([o, c], axis=1).min(axis=1)

    # A. bar anatomy ----------------------------------------------------------
    out["mf_body_rng"] = (body / rng_safe).mask(flat, 0.0)
    out["mf_body_pct"] = abs_body / open_safe * 100.0
    uw = (h - oc_max) / rng_safe  # wick fractions of range
    lw = (oc_min - low) / rng_safe
    out["mf_upper_wick"] = uw.mask(flat, 0.0)
    out["mf_lower_wick"] = lw.mask(flat, 0.0)
    out["mf_close_pos"] = ((c - low) / rng_safe).mask(flat, 0.5)
    a = atr(df, atr_period).astype(float)
    a.iloc[: max(int(atr_period), 1)] = np.nan  # ATR warmup -> NaN
    out["mf_range_atr"] = rng / a
    out["mf_dir"] = np.sign(body).astype(float)
    gap = (o - prev_c_safe) / prev_c_safe * 100.0
    if n:
        gap.iloc[0] = 0.0  # no previous close on bar 0
    out["mf_gap_pct"] = gap

    # B. bar sequencing / pattern flags ---------------------------------------
    dirn = np.sign(body).astype(float)
    prev_dir = dirn.shift(1)
    same = (dirn == prev_dir) & (dirn != 0)
    streak = same.astype(int).groupby((~same).cumsum()).cumsum() + 1
    out["mf_streak"] = streak.where(dirn != 0, 0.0).astype(float)
    flip = (dirn != prev_dir) & prev_dir.notna()
    out["mf_alt_count"] = flip.astype(float).rolling(10, min_periods=10).sum()

    prev_h = h.shift(1)
    prev_l = low.shift(1)
    prev_o = o.shift(1)
    cur_bull = c > o
    cur_bear = c < o
    prev_bull = prev_c > prev_o
    prev_bear = prev_c < prev_o
    out["mf_inside"] = ((h <= prev_h) & (low >= prev_l)).astype(float)
    out["mf_outside"] = ((h >= prev_h) & (low <= prev_l)).astype(float)
    out["mf_engulf_bull"] = (
        cur_bull & prev_bear & (c >= prev_o) & (o <= prev_c)).astype(float)
    out["mf_engulf_bear"] = (
        cur_bear & prev_bull & (c <= prev_c) & (o >= prev_o)).astype(float)
    out["mf_harami_bull"] = (
        cur_bull & prev_bear & (o > prev_c) & (c < prev_o)).astype(float)
    out["mf_harami_bear"] = (
        cur_bear & prev_bull & (o < prev_c) & (c > prev_o)).astype(float)

    bull3 = (cur_bull & cur_bull.shift(1, fill_value=False)
             & cur_bull.shift(2, fill_value=False))
    bear3 = (cur_bear & cur_bear.shift(1, fill_value=False)
             & cur_bear.shift(2, fill_value=False))
    out["mf_three_soldiers"] = (
        bull3 & (c > c.shift(1)) & (c.shift(1) > c.shift(2))).astype(float)
    out["mf_three_crows"] = (
        bear3 & (c < c.shift(1)) & (c.shift(1) < c.shift(2))).astype(float)

    doji = rng_pos & (abs_body <= 0.1 * rng)
    out["mf_doji"] = doji.astype(float)
    out["mf_doji_dragonfly"] = (doji & (lw >= 0.6) & (uw <= 0.15)).astype(float)
    out["mf_doji_gravestone"] = (doji & (uw >= 0.6) & (lw <= 0.15)).astype(float)
    out["mf_marubozu"] = (rng_pos & (abs_body >= 0.9 * rng)).astype(float)
    pin_bull = rng_pos & (lw >= 2.0 * abs_body / rng_safe) & (uw <= 0.3)
    pin_bear = rng_pos & (uw >= 2.0 * abs_body / rng_safe) & (lw <= 0.3)
    out["mf_pin_bull"] = pin_bull.astype(float)
    out["mf_pin_bear"] = pin_bear.astype(float)
    out["mf_hammer"] = (pin_bull & (c < c.shift(3))).astype(float)
    out["mf_shooting_star"] = (pin_bear & (c > c.shift(3))).astype(float)

    # C. bar-rate / activity ---------------------------------------------------
    ns = _utc_ns(idx)
    right = np.searchsorted(ns, ns, side="right")
    left = np.searchsorted(ns, ns - _HOUR_NS, side="right")
    out["mf_bars_per_hour"] = pd.Series((right - left).astype(float), index=idx)

    v_mean = v.rolling(20).mean()
    v_std = v.rolling(20).std()
    out["mf_vol_z"] = ((v - v_mean) / v_std).where(v_std != 0, 0.0)

    move = (c - prev_c).abs() / a
    out["mf_move_per_bar"] = move
    out["mf_accel"] = move.diff()

    # D. bar-movement dynamics --------------------------------------------------
    vel = (c - c.shift(3)) / (3.0 * a)
    out["mf_velocity"] = vel
    out["mf_accel2"] = vel.diff()
    out["mf_jerk"] = vel.diff().diff()

    d1 = c.diff()
    out["mf_mom_decay"] = d1.rolling(50).corr(d1.shift(1))
    r = c.pct_change()
    phi = (r.rolling(100).cov(r.shift(1)) / r.rolling(100).var()).clip(-0.99, 0.99)
    out["mf_ar1_phi"] = phi
    good = phi.notna() & (phi > 0) & (phi < 1)
    hl_ratio = -np.log(2.0) / np.log(phi.where(good))
    halflife = pd.Series(np.where(good, hl_ratio, 999.0), index=idx, dtype=float)
    halflife[phi.isna()] = np.nan  # warmup / zero-variance -> NaN, not sentinel
    out["mf_halflife"] = halflife

    # E. micro-timing ------------------------------------------------------------
    idxu = idx.tz_convert("UTC") if idx.tz is not None else idx
    out["mf_hour"] = pd.Series(idxu.hour, index=idx, dtype=float)
    out["mf_min_bucket"] = pd.Series(idxu.minute // 15, index=idx, dtype=float)
    hh = np.asarray(idxu.hour, dtype=int)
    mm = np.asarray(idxu.minute, dtype=int)
    sess_start = np.select([hh < 7, hh < 12, hh < 16, hh < 21],
                           [0, 7, 12, 16], default=21)  # == core/sessions.py
    out["mf_min_of_session"] = pd.Series(hh * 60 + mm - sess_start * 60,
                                         index=idx, dtype=float)
    out["mf_dow"] = pd.Series(idxu.dayofweek, index=idx, dtype=float)

    # F. path metrics (backward-looking only) -------------------------------------
    ad1 = d1.abs()
    for win in (20, 50):
        den = ad1.rolling(win).sum()
        er = c.diff(win).abs() / den.where(den > 0)
        out[f"mf_er_{win}"] = er.mask(den == 0, 0.0)

    path_sum = ad1.rolling(50).sum()
    span = h.rolling(50).max() - low.rolling(50).min()
    fd = 2.0 - np.log(path_sum.where(path_sum > 0)) / np.log(span.where(span > 0))
    out["mf_fd_50"] = fd.mask(path_sum <= 0, 1.5).mask(span <= 0, 1.5)

    s1 = d1.rolling(100).std()
    s5 = c.diff(5).rolling(100).std()
    lag_ratio = s5 / s1.where(s1 > 0)
    hurst = np.log(lag_ratio.where(lag_ratio > 0)) / np.log(5.0)
    out["mf_hurst_100"] = hurst.mask((s1 == 0) | (s5 == 0), 0.5)

    out["mf_path_mfe20"] = (h.rolling(20).max() - c) / a
    out["mf_path_mae20"] = (c - low.rolling(20).min()) / a

    # G. lower-timeframe enrichment -------------------------------------------------
    ticks, conc = _lower_tf_enrichment(idx, lower_tf)
    out["mf_ticks_per_bar"] = ticks
    out["mf_vol_conc"] = conc

    result = pd.DataFrame(out, index=idx)
    return result[MICRO_FEATURE_COLUMNS]


def validate_no_lookahead(df: pd.DataFrame, k: int, rtol: float = 1e-9) -> bool:
    """True iff rows < k of the full-frame features equal the prefix features.

    Computes compute_micro_features(df) and compute_micro_features(df.iloc[:k])
    and compares rows < k element-wise with numpy.isclose + NaN masks
    (NaN == NaN counts as equal; DataFrame.equals is NOT used because
    NaN != NaN there). Any value that depends on data beyond row t shows up
    as a mismatch and returns False.
    """
    full = compute_micro_features(df)
    part = compute_micro_features(df.iloc[:k])
    a = full.iloc[:k].to_numpy(dtype=float)
    b = part.to_numpy(dtype=float)
    if a.shape != b.shape:
        return False
    close = np.isclose(a, b, rtol=rtol, atol=1e-12, equal_nan=False)
    both_nan = np.isnan(a) & np.isnan(b)
    return bool((close | both_nan).all())
