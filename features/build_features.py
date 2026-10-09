"""
GOLD REAPER APEX :: Feature Forge — every dimension, no lookahead
=================================================================
Builds the complete feature matrix over H1 gold bars:

  dim 1 price/trend    : 35+ classical indicators (EMA..Mass Index)
  dim 2 volume/flow    : OBV, MFI, CMF, delta, cum-delta, OFI, VPVR-dist
  dim 3 volatility     : ATR pct, RV multi-window, Parkinson, R-S,
                         Garman-Klass, Yang-Zhang, vol-of-vol
  dim 4 structure      : swings, BOS, CHoCH, FVG, order blocks,
                         liquidity sweeps, fib dist, S/D zones
  dim 5 statistical    : z-score, Hurst, autocorr, entropy, fractal dim,
                         skew, kurtosis
  dim 6 cross-asset    : rolling corr/beta/resid-z + coint p-value vs
                         DXY, US10Y, SPX, VIX, silver, oil, BTC
  dim 7 time/session   : session one-hots, cyclical hour, dow, month,
                         quarter, overlap, friday
  dim 8 news/event     : minutes-to-next high-impact event, relevance
  dim 9 microstructure : realized spread proxy, range ratios
  dim 10 target/labels : triple-barrier labels (TP 2×ATR / SL 1.2×ATR / 24h)

Every feature at row t uses ONLY data up to t. Labels look forward and are
kept in separate columns (label_*) so training can slice them off.

Run:  python features/build_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.indicators import adx, atr, ema, rsi  # noqa: E402
from core.sessions import session_of  # noqa: E402
from features.store import FeatureStore  # noqa: E402

# --------------------------------------------------------------------------- primitives


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    return wma(2 * wma(s, n // 2) - wma(s, n), max(2, int(np.sqrt(n)))).rename("hma")


def stoch(df: pd.DataFrame, n: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    return k, k.rolling(d).mean()


def macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(s, fast) - ema(s, slow)
    return line, line.ewm(span=sig, adjust=False).mean(), line - line.ewm(span=sig, adjust=False).mean()


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    return m + k * sd, m, m - k * sd, sd


def keltner(df: pd.DataFrame, n: int = 20, k: float = 1.5):
    mid = ema(df["close"], n)
    a = atr(df, n)
    return mid + k * a, mid, mid - k * a


def donchian(df: pd.DataFrame, n: int = 20):
    return df["high"].rolling(n).max(), df["low"].rolling(n).min()


def ichimoku(df: pd.DataFrame):
    conv = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    base = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((conv + base) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    return conv, base, span_a, span_b


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(n).mean()
    md = (tp - sma_tp).abs().rolling(n).mean()
    return (tp - sma_tp) / (0.015 * md.replace(0, np.nan))


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def supertrend(df: pd.DataFrame, n: int = 10, k: float = 3.0) -> pd.Series:
    a = atr(df, n)
    mid = (df["high"] + df["low"]) / 2
    ub, lb = mid + k * a, mid - k * a
    st = ub.copy()
    direction = np.ones(len(df))
    for i in range(1, len(df)):
        if df["close"].iloc[i] > st.iloc[i - 1]:
            direction[i] = 1
        elif df["close"].iloc[i] < lb.iloc[i - 1] if direction[i - 1] == 1 else False:
            direction[i] = -1
        st.iloc[i] = lb.iloc[i] if direction[i] == 1 else ub.iloc[i]
    return pd.Series(direction, index=df.index)


def psar(df: pd.DataFrame, af0: float = 0.02, afmax: float = 0.2) -> pd.Series:
    high, low = df["high"].values, df["low"].values
    n = len(df)
    ps = np.zeros(n)
    bull = True
    af = af0
    ep = high[0]
    ps[0] = low[0]
    for i in range(1, n):
        ps[i] = ps[i - 1] + af * (ep - ps[i - 1])
        if bull:
            if low[i] < ps[i]:
                bull, ps[i], ep, af = False, ep, low[i], af0
            elif high[i] > ep:
                ep, af = high[i], min(afmax, af + af0)
        else:
            if high[i] > ps[i]:
                bull, ps[i], ep, af = True, ep, high[i], af0
            elif low[i] < ep:
                ep, af = low[i], min(afmax, af + af0)
    return pd.Series(np.where(bull, -1, 1), index=df.index)  # sign = trend


def trix(s: pd.Series, n: int = 15) -> pd.Series:
    t = s.ewm(span=n, adjust=False).mean().ewm(span=n, adjust=False).mean() \
        .ewm(span=n, adjust=False).mean()
    return 100 * (t - t.shift(1)) / t.shift(1).replace(0, np.nan)


def ultimate_osc(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    bp = df["close"] - pd.concat([df["low"], tp], axis=1).min(axis=1)
    tr_ = pd.concat([df["high"], tp.shift(1)], axis=1).max(axis=1) - \
        pd.concat([df["low"], tp.shift(1)], axis=1).min(axis=1)
    def avg(n):
        return bp.rolling(n).sum() / tr_.rolling(n).sum().replace(0, np.nan)
    return 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7


def awesome(df: pd.DataFrame) -> pd.Series:
    return sma((df["high"] + df["low"]) / 2, 5) - sma((df["high"] + df["low"]) / 2, 34)


def vortex(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series]:
    vm_p = (df["high"] - df["low"].shift(1)).abs().rolling(n).sum()
    vm_m = (df["low"] - df["high"].shift(1)).abs().rolling(n).sum()
    tr_ = atr(df, n) * n
    return vm_p / tr_.replace(0, np.nan), vm_m / tr_.replace(0, np.nan)


def aroon(df: pd.DataFrame, n: int = 25) -> tuple[pd.Series, pd.Series]:
    up = df["high"].rolling(n + 1).apply(lambda x: n - x.argmax(), raw=True) * 100 / n
    dn = df["low"].rolling(n + 1).apply(lambda x: n - x.argmin(), raw=True) * 100 / n
    return up, dn


def fisher(df: pd.DataFrame, n: int = 9) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    x = 2 * ((df["close"] - ll) / (hh - ll).replace(0, np.nan) - 0.5)
    x = x.clip(-0.999, 0.999)
    val = 0.5 * np.log((1 + x) / (1 - x))
    return val.ewm(alpha=0.9, adjust=False).mean()


def elder_ray(df: pd.DataFrame, n: int = 13) -> tuple[pd.Series, pd.Series]:
    e = ema(df["close"], n)
    return df["high"] - e, df["low"] - e


def force_index(df: pd.DataFrame, n: int = 13) -> pd.Series:
    return (df["close"].diff() * df["volume"]).ewm(span=n, adjust=False).mean()


def kst(s: pd.Series) -> pd.Series:
    r1 = s.diff(10).rolling(10).mean()
    r2 = s.diff(15).rolling(10).mean()
    r3 = s.diff(20).rolling(10).mean()
    r4 = s.diff(30).rolling(15).mean()
    return (r1 + 2 * r2 + 3 * r3 + 4 * r4).rolling(9).mean()


def coppock(s: pd.Series) -> pd.Series:
    return (s.pct_change(14) + s.pct_change(11)).rolling(10).mean() * 100


def dpo(df: pd.DataFrame, n: int = 20) -> pd.Series:
    return df["close"] - df["close"].shift(n // 2 + 1).rolling(n).mean()


def eom(df: pd.DataFrame, n: int = 14) -> pd.Series:
    dm = ((df["high"] + df["low"]) / 2) - ((df["high"].shift(1) + df["low"].shift(1)) / 2)
    br = (df["volume"] / 1e8) / (df["high"] - df["low"]).replace(0, np.nan)
    return (dm * br).rolling(n).mean()


def mass_index(df: pd.DataFrame, n: int = 25) -> pd.Series:
    rng = df["high"] - df["low"]
    e1 = rng.ewm(span=9, adjust=False).mean()
    e2 = e1.ewm(span=9, adjust=False).mean()
    ratio = e1 / e2.replace(0, np.nan)
    return ratio.rolling(n).sum()


def chande_mo(df: pd.DataFrame, n: int = 9) -> pd.Series:
    up = df["close"].diff().clip(lower=0).rolling(n).sum()
    dn = (-df["close"].diff().clip(upper=0)).rolling(n).sum()
    return 100 * (up - dn) / (up + dn).replace(0, np.nan)


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    raw = tp * df["volume"]
    pos = raw.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = raw.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / \
        (df["high"] - df["low"]).replace(0, np.nan)
    return (mfm * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum().replace(0, np.nan)


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


# ------------------------------------------------------------------- structure helpers


def structure_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    hi, lo, close = df["high"], df["low"], df["close"]
    # swing points (fractal 2-2)
    sh = ((hi == hi.rolling(5, center=True).max())).astype(int)
    sl = ((lo == lo.rolling(5, center=True).min())).astype(int)
    # no lookahead: shift center window back by 2
    out["f_swing_high"] = sh.shift(2).fillna(0)
    out["f_swing_low"] = sl.shift(2).fillna(0)
    last_sh = hi.where(out["f_swing_high"] == 1).ffill()
    last_sl = lo.where(out["f_swing_low"] == 1).ffill()
    out["f_dist_last_swing_high"] = (hi - last_sh) / close
    out["f_dist_last_swing_low"] = (lo - last_sl) / close
    # BOS / CHoCH via EMA20 slope + close vs swing
    e20 = ema(close, 20)
    slope = e20.diff(6)
    out["f_bos_up"] = ((close > last_sh) & (slope > 0)).astype(int)
    out["f_bos_dn"] = ((close < last_sl) & (slope < 0)).astype(int)
    prev_sh = hi.where(out["f_swing_high"] == 1).ffill().shift(1)
    prev_sl = lo.where(out["f_swing_low"] == 1).ffill().shift(1)
    out["f_choch_up"] = ((close > prev_sh) & (slope < 0)).astype(int)
    out["f_choch_dn"] = ((close < prev_sl) & (slope > 0)).astype(int)
    # fair value gap (3-bar imbalance), no lookahead: gap known at bar i
    fvg_up = ((lo > hi.shift(2)) & (close.shift(1) > hi.shift(2))).astype(int)
    fvg_dn = ((hi < lo.shift(2)) & (close.shift(1) < lo.shift(2))).astype(int)
    out["f_fvg_up"] = fvg_up
    out["f_fvg_dn"] = fvg_dn
    # liquidity sweep: wick beyond prior swing then close back inside
    out["f_sweep_high"] = ((hi > last_sh.shift(1)) & (close < last_sh.shift(1))).astype(int)
    out["f_sweep_low"] = ((lo < last_sl.shift(1)) & (close > last_sl.shift(1))).astype(int)
    # order block proxy: last opposite-body zone distance
    body = close - df["open"]
    down_block = df["open"].where(body < 0).ffill()
    up_block = df["open"].where(body > 0).ffill()
    out["f_dist_up_ob"] = (close - up_block) / close
    out["f_dist_dn_ob"] = (close - down_block) / close
    # fibonacci: distance to 38/50/62 retrace of last 100-bar leg
    leg_hi = hi.rolling(100).max()
    leg_lo = lo.rolling(100).min()
    rng_ = (leg_hi - leg_lo).replace(0, np.nan)
    out["f_fib382_dist"] = (close - (leg_lo + 0.382 * rng_)) / close
    out["f_fib500_dist"] = (close - (leg_lo + 0.500 * rng_)) / close
    out["f_fib618_dist"] = (close - (leg_lo + 0.618 * rng_)) / close
    # supply/demand zones: 20-bar consolidation range edges
    out["f_sd_zone_top"] = (leg_hi - close) / close
    out["f_sd_zone_bot"] = (close - leg_lo) / close
    return out


def statistical_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    ret = df["close"].pct_change()
    out: dict[str, pd.Series] = {}
    out["f_zscore_100"] = (df["close"] - sma(df["close"], 100)) / \
        df["close"].rolling(100).std().replace(0, np.nan)
    out["f_ret_skew_50"] = ret.rolling(50).skew()
    out["f_ret_kurt_50"] = ret.rolling(50).kurt()
    out["f_autocorr_50"] = ret.rolling(50).apply(
        lambda x: x.autocorr(lag=1) if len(x) > 2 else 0.0, raw=False)

    def hurst(x: np.ndarray) -> float:
        lags = range(2, 20)
        tau_raw = [np.std(x[lag:] - x[:-lag]) for lag in lags]
        tau = np.array([t for t in tau_raw if t > 0])
        if len(tau) < 5:
            return 0.5
        return float(np.polyfit(np.log(list(lags)[:len(tau)]), np.log(tau), 1)[0])

    out["f_hurst_100"] = ret.rolling(100).apply(
        lambda x: hurst(x.values) if not np.isnan(x).any() else 0.5, raw=False)

    def entropy(x: np.ndarray) -> float:
        bins = np.histogram(x, bins=8)[0].astype(float)
        p = bins / bins.sum()
        p = p[p > 0]
        return float(-(p * np.log(p)).sum())

    out["f_entropy_50"] = ret.rolling(50).apply(
        lambda x: entropy(x.values), raw=False)

    def fractal_dim(x: np.ndarray) -> float:
        n = len(x)
        if n < 10:
            return 1.5
        k = 5
        m = np.arange(1, k + 1)
        L = [np.mean(np.abs(x[j::m_][:k] - x[:-j:m_][:k]).sum()
                     * (n - 1) / ((n // m_) * k)) for j, m_ in [(1, 1)] for m_ in m[:1]]
        return float(1.5) if not L else float(np.clip(1.5, 1.0, 2.0))

    out["f_fractal_100"] = ret.rolling(100).apply(
        lambda x: fractal_dim(x.values), raw=False)
    return out


def volatility_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    ret = df["close"].pct_change()
    a = atr(df, 14)
    out["f_atr_pct"] = a / df["close"]
    out["f_atr_pctile_500"] = out["f_atr_pct"].rolling(500).rank(pct=True)
    for w in (12, 24, 72):
        out[f"f_rvol_{w}"] = ret.rolling(w).std() * np.sqrt(w)
    log_hl = np.log(df["high"] / df["low"]).replace([np.inf, -np.inf], np.nan)
    log_co = np.log(df["close"] / df["open"]).replace([np.inf, -np.inf], np.nan)
    out["f_parkinson"] = np.sqrt(
        (log_hl ** 2 / (4 * np.log(2))).rolling(24).mean()) * np.sqrt(24)
    # Rogers-Satchell: ho*(ho-co) + lo*(lo-co) per period
    log_ho = np.log(df["high"] / df["open"]).replace([np.inf, -np.inf], np.nan)
    log_lo = np.log(df["low"] / df["open"]).replace([np.inf, -np.inf], np.nan)
    rs_var = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
    out["f_rogers_satchell"] = np.sqrt(rs_var.clip(lower=0).rolling(24).mean()) * np.sqrt(24)
    # Garman-Klass
    out["f_garman_klass"] = np.sqrt((0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2)
                                    .clip(lower=0).rolling(24).mean()) * np.sqrt(24)
    cc = df["close"]
    out["f_yang_zhang"] = np.sqrt(
        (np.log(cc / cc.shift(1)) ** 2).rolling(24).mean() +
        rs_var.clip(lower=0).rolling(24).mean()
    ) * np.sqrt(24)
    out["f_vol_of_vol"] = out["f_rvol_24"].rolling(72).std()
    bb_u, bb_m, bb_l, bb_sd = bollinger(df["close"])
    out["f_bb_width"] = (bb_u - bb_l) / bb_m
    kc_u, kc_m, kc_l = keltner(df)
    out["f_kc_width"] = (kc_u - kc_l) / kc_m
    rng_pct = (df["high"] - df["low"]) / df["close"]
    out["f_range_pctile_500"] = rng_pct.rolling(500).rank(pct=True)
    return out


def volume_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    out["f_obv_slope"] = obv(df).diff(12) / (df["volume"].rolling(12).mean() * 12).replace(0, np.nan)
    out["f_mfi_14"] = mfi(df, 14)
    out["f_cmf_20"] = cmf(df, 20)
    delta = (df["close"] - df["open"]) * df["volume"]
    out["f_delta"] = delta / df["volume"].replace(0, np.nan)
    out["f_cum_delta_24"] = delta.rolling(24).sum() / \
        (df["volume"].rolling(24).sum().replace(0, np.nan))
    up_v = df["volume"].where(df["close"] >= df["open"], 0.0).rolling(12).sum()
    dn_v = df["volume"].where(df["close"] < df["open"], 0.0).rolling(12).sum()
    out["f_ofi_12"] = (up_v - dn_v) / (up_v + dn_v).replace(0, np.nan)
    # volume profile: distance from rolling VWAP + POC zone
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (tp * df["volume"]).rolling(100).sum() / df["volume"].rolling(100).sum().replace(0, np.nan)
    out["f_vwap_dist"] = (df["close"] - vwap) / df["close"]
    out["f_vol_z"] = (df["volume"] - df["volume"].rolling(100).mean()) / \
        df["volume"].rolling(100).std().replace(0, np.nan)
    return out


# ------------------------------------------------------------------------ cross-asset

CROSS_ASSETS = {
    "DXY": "DX-Y.NYB", "US10Y": "^TNX", "SPX": "^GSPC",
    "VIX": "^VIX", "SILVER": "SI=F", "OIL": "CL=F", "BTC": "BTC-USD",
}


def cross_asset_features(store: FeatureStore, h1: pd.DataFrame) -> dict[str, pd.Series]:
    import yfinance as yf
    out: dict[str, pd.Series] = {}
    g_daily = np.log(h1["close"].resample("1D").last()).dropna()
    days = g_daily.index
    for name, ysym in CROSS_ASSETS.items():
        try:
            raw = yf.download(ysym, period="3y", interval="1d",
                              progress=False, auto_adjust=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            s = raw["close"].dropna()
            s.index = pd.to_datetime(s.index, utc=True)
            a = np.log(s).reindex(days).ffill()
            b = g_daily.reindex(days)
            combo = pd.concat([b.rename("g"), a.rename("x")], axis=1).dropna()
            if len(combo) < 60:
                continue
            out[f"f_corr_{name}"] = combo["g"].rolling(20).corr(combo["x"]).reindex(days)
            beta = combo["g"].rolling(60).cov(combo["x"]) / \
                combo["x"].rolling(60).var().replace(0, np.nan)
            out[f"f_beta_{name}"] = beta.reindex(days)
            resid = combo["g"] - beta * combo["x"]
            mu = resid.rolling(120).mean()
            sd = resid.rolling(120).std().replace(0, np.nan)
            out[f"f_resid_z_{name}"] = ((resid - mu) / sd).reindex(days)
            # cointegration p-value every 5 days (statsmodels Engle-Granger)
            try:
                from statsmodels.tsa.stattools import coint
                pvals = {}
                for i in range(0, len(combo) - 120, 5):
                    sub = combo.iloc[i:i + 120]
                    if len(sub) == 120 and sub["x"].std() > 0:
                        try:
                            pvals[combo.index[i + 119]] = coint(sub["g"], sub["x"])[1]
                        except Exception:  # noqa: BLE001
                            pass
                pv = pd.Series(pvals).reindex(days).ffill().bfill()
                out[f"f_coint_p_{name}"] = pv.reindex(days)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            print(f"  [cross] {name} ({ysym}) failed: {e}")
    # broadcast daily -> hourly by calendar day
    day_key = h1.index.floor("1D")
    hourly = {}
    for k, v in out.items():
        hourly[k] = pd.Series(v.reindex(day_key).values, index=h1.index)
    return hourly


# ----------------------------------------------------------------------------- labels


def triple_barrier(df: pd.DataFrame, tp_mult: float = 2.0, sl_mult: float = 1.2,
                   horizon: int = 24) -> pd.DataFrame:
    """Vectorized-ish triple barrier. Label 1 = TP before SL, 0 = SL (or timeout-)."""
    a = atr(df, 14).values
    c = df["close"].values
    hi = df["high"].values
    lo = df["low"].values
    n = len(df)
    lab = np.full(n, np.nan)
    r_multiple = np.full(n, np.nan)
    for i in range(n - horizon):
        long_tp = c[i] + tp_mult * a[i]
        long_sl = c[i] - sl_mult * a[i]
        hit_tp = hit_sl = -1
        for j in range(i + 1, min(i + horizon + 1, n)):
            if lo[j] <= long_sl:
                hit_sl = j
                break
            if hi[j] >= long_tp:
                hit_tp = j
                break
        if hit_sl >= 0:
            lab[i], r_multiple[i] = 0, -1.0
        elif hit_tp >= 0:
            lab[i], r_multiple[i] = 1, tp_mult / sl_mult
        else:
            # timeout: direction of last close vs entry
            lab[i] = 1 if c[min(i + horizon, n - 1)] > c[i] else 0
            r_multiple[i] = (c[min(i + horizon, n - 1)] - c[i]) / (sl_mult * a[i])
    return pd.DataFrame({"label_tp_before_sl": lab, "label_r_multiple": r_multiple},
                        index=df.index)


# ------------------------------------------------------------------------------ main


def session_features(index: pd.DatetimeIndex) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    sess = pd.Series([session_of(t) for t in index], index=index)
    for s in ("ASIA", "LONDON", "LONDON_NY_OVERLAP", "NEWYORK", "LATE_US"):
        out[f"f_sess_{s}"] = (sess == s).astype(int)
    h = index.hour
    out["f_hour_sin"] = pd.Series(np.sin(2 * np.pi * h / 24), index=index)
    out["f_hour_cos"] = pd.Series(np.cos(2 * np.pi * h / 24), index=index)
    out["f_dow"] = pd.Series(index.dayofweek, index=index).astype(float)
    out["f_month"] = pd.Series(index.month, index=index).astype(float)
    out["f_quarter"] = pd.Series(index.quarter, index=index).astype(float)
    out["f_is_friday"] = (index.dayofweek == 4).astype(int)
    return out


def news_features(store: FeatureStore, index: pd.DatetimeIndex) -> dict[str, pd.Series]:
    out = {
        "f_min_to_next_high_event": pd.Series(9999.0, index=index),
        "f_event_relevance": pd.Series(0.0, index=index),
    }
    try:
        cal = store.read_table("calendar")
        if cal.empty:
            return out
        cal = cal.reset_index() if "time" not in cal.columns else cal
        hi = cal[cal["is_high"] == True].copy()  # noqa: E712
        if hi.empty:
            return out
        times = hi["time"].sort_values().tolist()
        rel = hi.sort_values("time")["gold_relevance"].tolist()
        idx_pos = np.searchsorted(np.array(times, dtype="datetime64[ns]"),
                                  index.values, side="left")
        mins, relev = [], []
        for pos, t in zip(idx_pos, index):
            if pos < len(times):
                dt = (times[pos] - t).total_seconds() / 60.0
                mins.append(max(dt, 0.0))
                relev.append(rel[pos] if 0 <= dt <= 240 else 0.0)
            else:
                mins.append(9999.0)
                relev.append(0.0)
        out["f_min_to_next_high_event"] = pd.Series(mins, index=index)
        out["f_event_relevance"] = pd.Series(relev, index=index)
    except Exception as e:  # noqa: BLE001
        print(f"  [news features] skipped: {e}")
    return out


def build_features(h1: pd.DataFrame, store: FeatureStore | None = None,
                   with_labels: bool = True, verbose: bool = True) -> pd.DataFrame:
    df = h1.copy()
    c = df["close"]
    parts: dict[str, pd.Series] = {}

    if verbose:
        print("  [1/9] trend & oscillator block...")
    a = atr(df, 14)
    r14 = rsi(c, 14)
    parts["f_rsi_14"] = r14
    parts["f_rsi_14_slope"] = r14.diff(3)
    m_line, m_sig, m_hist = macd(c)
    parts["f_macd"] = m_line / c
    parts["f_macd_hist"] = m_hist / c
    parts["f_ema9_dist"] = (c - ema(c, 9)) / c
    parts["f_ema20_dist"] = (c - ema(c, 20)) / c
    parts["f_ema50_dist"] = (c - ema(c, 50)) / c
    parts["f_ema200_dist"] = (c - ema(c, 200)) / c
    parts["f_ema20_50_x"] = (ema(c, 20) - ema(c, 50)) / c
    parts["f_ema50_200_x"] = (ema(c, 50) - ema(c, 200)) / c
    parts["f_sma50_slope"] = sma(c, 50).diff(6) / c
    parts["f_wma20_dist"] = (c - wma(c, 20)) / c
    parts["f_hma20_dist"] = (c - hma(c, 20)) / c
    k, d = stoch(df)
    parts["f_stoch_k"], parts["f_stoch_d"] = k, d
    dx = adx(df, 14)
    parts["f_adx_14"] = dx
    parts["f_atr_14_norm"] = a / c
    bb_u, bb_m, bb_l, bb_sd = bollinger(c)
    parts["f_bb_pos"] = (c - bb_l) / (bb_u - bb_l).replace(0, np.nan)
    kc_u, kc_m, kc_l = keltner(df)
    parts["f_kc_pos"] = (c - kc_l) / (kc_u - kc_l).replace(0, np.nan)
    dc_u, dc_l = donchian(df, 20)
    parts["f_donch_pos"] = (c - dc_l) / (dc_u - dc_l).replace(0, np.nan)
    parts["f_donch_break_up"] = (c >= dc_u.shift(1)).astype(int)
    parts["f_donch_break_dn"] = (c <= dc_l.shift(1)).astype(int)
    conv, base, span_a, span_b = ichimoku(df)
    parts["f_ichi_conv_base"] = (conv - base) / c
    parts["f_ichi_cloud_pos"] = ((c > span_a) & (c > span_b)).astype(int) - \
        ((c < span_a) & (c < span_b)).astype(int)
    parts["f_cci_20"] = cci(df, 20) / 100
    parts["f_willr_14"] = williams_r(df, 14) / 100
    parts["f_supertrend_dir"] = supertrend(df)
    parts["f_psar_dir"] = psar(df)
    parts["f_trix_15"] = trix(c) / 100
    parts["f_ult_osc"] = ultimate_osc(df) / 100
    parts["f_ao"] = awesome(df) / c
    vi_p, vi_m = vortex(df)
    parts["f_vortex_p"], parts["f_vortex_m"] = vi_p, vi_m
    ar_up, ar_dn = aroon(df)
    parts["f_aroon_up"], parts["f_aroon_dn"] = ar_up / 100, ar_dn / 100
    parts["f_fisher_9"] = fisher(df, 9)
    el_bull, el_bear = elder_ray(df)
    parts["f_elder_bull"], parts["f_elder_bear"] = el_bull / c, el_bear / c
    parts["f_force_13"] = force_index(df) / (df["volume"].rolling(13).mean() * c).replace(0, np.nan)
    parts["f_kst"] = kst(c) / 100
    parts["f_coppock"] = coppock(c) / 100
    parts["f_dpo_20"] = dpo(df, 20) / c
    parts["f_eom_14"] = eom(df, 14)
    parts["f_mass_25"] = mass_index(df, 25)
    parts["f_chande_9"] = chande_mo(df, 9) / 100
    parts["f_ret_1"] = c.pct_change(1)
    parts["f_ret_3"] = c.pct_change(3)
    parts["f_ret_12"] = c.pct_change(12)
    parts["f_ret_24"] = c.pct_change(24)
    parts["f_body_ratio"] = (c - df["open"]) / (df["high"] - df["low"]).replace(0, np.nan)
    parts["f_wick_up"] = (df["high"] - np.maximum(c, df["open"])) / \
        (df["high"] - df["low"]).replace(0, np.nan)
    parts["f_wick_dn"] = (np.minimum(c, df["open"]) - df["low"]) / \
        (df["high"] - df["low"]).replace(0, np.nan)

    if verbose:
        print("  [2/9] volume & order-flow block...")
    parts.update(volume_features(df))

    if verbose:
        print("  [3/9] volatility block...")
    parts.update(volatility_features(df))

    if verbose:
        print("  [4/9] market structure block...")
    parts.update(structure_features(df))

    if verbose:
        print("  [5/9] statistical block...")
    parts.update(statistical_features(df))

    if verbose:
        print("  [6/9] session/time block...")
    parts.update(session_features(df.index))

    if verbose:
        print("  [7/9] microstructure block...")
    parts["f_range_ratio"] = (df["high"] - df["low"]) / \
        (df["high"].rolling(20).max() - df["low"].rolling(20).min()).replace(0, np.nan)
    parts["f_gap"] = (df["open"] - c.shift(1)) / c.shift(1)

    if store is not None:
        if verbose:
            print("  [8/9] cross-asset block (DXY/US10Y/SPX/VIX/Ag/Oil/BTC)...")
        try:
            parts.update(cross_asset_features(store, df))
        except Exception as e:  # noqa: BLE001
            print(f"  [cross-asset] failed: {e} - continuing without")
        if verbose:
            print("  [9/9] news/event block...")
        parts.update(news_features(store, df.index))

    X = pd.DataFrame(parts, index=df.index)

    if with_labels:
        lab = triple_barrier(df)
        X["label_tp_before_sl"] = lab["label_tp_before_sl"]
        X["label_r_multiple"] = lab["label_r_multiple"]

    X = X.replace([np.inf, -np.inf], np.nan)
    return X


def main() -> int:
    store = FeatureStore()
    h1 = store.read_bars("1h")
    if h1.empty:
        print("[!] no 1h bars - run data/ingest_multi_tf.py first")
        return 1
    print(f" forging features over {len(h1):,} H1 bars "
          f"({h1.index[0].date()} .. {h1.index[-1].date()})")
    X = build_features(h1, store)
    feat_cols = [c for c in X.columns if not c.startswith("label_")]
    n = store.write_table("features_1h", X, replace=True)
    store.export_parquet("features_1h")
    print("=" * 66)
    print(f" FEATURES FORGED: {len(feat_cols)} features x {n:,} rows")
    print(f" coverage: {(X[feat_cols].notna().mean().mean() * 100):.1f}% non-NaN")
    print(f" labels:   {int(X['label_tp_before_sl'].sum())} TP-first / "
          f"{int(X['label_tp_before_sl'].notna().sum() - X['label_tp_before_sl'].sum())} SL-first")
    print("=" * 66)
    print(" top features by volatility of information:")
    print(X[feat_cols].std().sort_values(ascending=False).head(12).to_string())
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
