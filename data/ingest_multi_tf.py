"""
GOLD REAPER APEX :: Multi-Timeframe Ingestion Engine
====================================================
Ingests gold (GC=F futures + XAUUSD=X spot) across every usable timeframe
and lands everything in the DuckDB feature store:

    1m (7d) · 2m (60d) · 5m (60d) · 15m (60d) · 30m (60d) · 1h (730d)
    4h (derived) · 1d (20y) · 1wk (max) · 1mo (max) · 3mo/1y (derived)

Yahoo limits intraday depth — the tick layer (ingest_ticks.py, Dukascopy)
covers deeper history where available. Run:  python data/ingest_multi_tf.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.store import FeatureStore  # noqa: E402

SYMBOL = "GC=F"
PLAN = [  # (interval, yf period, store tf)
    ("1m", "7d", "1m"),
    ("2m", "60d", "2m"),
    ("5m", "60d", "5m"),
    ("15m", "60d", "15m"),
    ("30m", "60d", "30m"),
    ("60m", "730d", "1h"),
    ("1d", "20y", "1d"),
    ("1wk", "max", "1wk"),
    ("1mo", "max", "1mo"),
]


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols].dropna(subset=["close"])
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    return df[~df.index.duplicated(keep="last")]


def fetch(interval: str, period: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(SYMBOL, period=period, interval=interval,
                     progress=False, auto_adjust=False)
    return _norm(df)


def derive(df_1h: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df_1h.empty:
        return pd.DataFrame()
    out = df_1h.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"}).dropna()
    return out


def main() -> int:
    store = FeatureStore()
    print("=" * 66)
    print(" GOLD REAPER APEX :: multi-timeframe ingestion")
    print("=" * 66)
    total = 0
    h1 = pd.DataFrame()
    for interval, period, tf in PLAN:
        for attempt in range(2):
            try:
                df = fetch(interval, period)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  retry {tf}: {e}")
                time.sleep(2)
        else:
            df = pd.DataFrame()
        n = store.write_bars(tf, df) if not df.empty else 0
        total += n
        got = f"{len(df):>7,} bars" if not df.empty else "EMPTY"
        print(f"  {tf:>4} (yf {interval:>3} {period:>5}) -> {got} written {n:,}")
        if tf == "1h":
            h1 = df
        time.sleep(1.0)

    if not h1.empty:
        for rule, tf in (("4h", "4h"),):
            n = store.write_bars(tf, derive(h1, rule))
            print(f"  {tf:>4} (derived from 1h)             -> {len(df) and ''}{n:,} bars")
    elif store.read_bars("1h").empty:
        print("  [!] no 1h data anywhere - intraday layers empty this run")

    print("-" * 66)
    print(store.stats().to_string(index=False))
    store.close()
    print(f" total rows written: {total:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
