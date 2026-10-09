"""
GOLD REAPER :: Indicator Arsenal (pure pandas, zero dependencies)
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0.0, pd.NA)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0).infer_objects(copy=False)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([
        (h - l),
        (h - prev_c).abs(),
        (l - prev_c).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(
        [u if (u > d and u > 0) else 0.0 for u, d in zip(up, dn)], index=df.index).astype(float)
    minus_dm = pd.Series(
        [d if (d > u and d > 0) else 0.0 for u, d in zip(up, dn)], index=df.index).astype(float)
    tr = atr(df, period)
    tr_safe = tr.replace(0.0, float("nan")).astype(float)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_safe
    denom = (plus_di + minus_di).replace(0.0, float("nan")).astype(float)
    dx = (plus_di - minus_di).abs() / denom * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0).astype(float)


def swing_levels(df: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    return float(df["high"].rolling(lookback).max().iloc[-1]), \
           float(df["low"].rolling(lookback).min().iloc[-1])


def bullish_body(row) -> bool:
    return float(row["close"]) > float(row["open"])


def bearish_body(row) -> bool:
    return float(row["close"]) < float(row["open"])
