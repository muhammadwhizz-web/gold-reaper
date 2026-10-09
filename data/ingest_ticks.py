"""
GOLD REAPER APEX :: Tick-Level Ingestion (Dukascopy .bi5 decoder)
=================================================================
Dukascopy Bank publishes free institutional-grade XAUUSD tick history as
LZMA-compressed binary files, one per hour:

    https://datafeed.dukascopy.com/datafeed/XAUUSD/{yyyy}/{MM-1}/{dd}/{HH}h_ticks.bi5
      - month is 0-INDEXED in the URL (January = 00)
      - each decompressed record = 20 bytes:
          uint32  ms offset inside the hour
          uint32  ask  (raw points, XAUUSD point = 0.001)
          uint32  bid  (raw points)
          float32 volume (millions, 0 = indicative quote tick)

This module downloads, decodes, aggregates (1s/1m bars) and lands the
result in the DuckDB store. No API key. Real ticks, real history.

Run:
    python data/ingest_ticks.py                       # last 3 days of ticks
    python data/ingest_ticks.py 2024-01-01 2024-03-31 # explicit range -> 1s bars
"""
from __future__ import annotations

import lzma
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.store import FeatureStore  # noqa: E402

BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD"
POINT = 1000.0          # XAUUSD stored as integer points of 0.001
REC = struct.Struct(">I I I f")   # ms, ask, bid, volume
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) GoldReaperApex/2.0"}


def _url(ts: datetime) -> str:
    return f"{BASE}/{ts.year}/{ts.month - 1:02d}/{ts.day:02d}/{ts.hour:02d}h_ticks.bi5"


def fetch_hour(ts: datetime) -> pd.DataFrame:
    """Download + decode one hour of XAUUSD ticks. Returns tz-UTC tick frame."""
    req = urllib.request.Request(_url(ts), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if not raw:
        return pd.DataFrame()
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        # some files are stored raw when empty
        data = raw
    n = len(data) // REC.size
    if n == 0:
        return pd.DataFrame()
    rows = [REC.unpack_from(data, i * REC.size) for i in range(n)]
    hour_start = ts.replace(minute=0, second=0, microsecond=0)
    times = [hour_start + timedelta(milliseconds=r[0]) for r in rows]
    return pd.DataFrame({
        "ask": [r[1] / POINT for r in rows],
        "bid": [r[2] / POINT for r in rows],
        "volume": [r[3] for r in rows],
    }, index=pd.DatetimeIndex(times, name="time", tz="UTC"))


def fetch_range(start: datetime, end: datetime,
                sleep: float = 0.15) -> pd.DataFrame:
    """Fetch ticks for [start, end) hour by hour. UTC."""
    cur = start.replace(minute=0, second=0, microsecond=0)
    frames = []
    while cur < end:
        try:
            f = fetch_hour(cur)
            if not f.empty:
                frames.append(f)
        except Exception as e:  # noqa: BLE001
            print(f"  [ticks] {cur:%Y-%m-%d %H:00} failed: {e}")
        cur += timedelta(hours=1)
        time.sleep(sleep)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="first")]


def ticks_to_seconds(ticks: pd.DataFrame) -> pd.DataFrame:
    """Aggregate ticks to 1-second OHLC bars (mid price)."""
    if ticks.empty:
        return pd.DataFrame()
    mid = (ticks["ask"] + ticks["bid"]) / 2
    agg = pd.DataFrame({"mid": mid, "volume": ticks["volume"]})
    bars = agg.resample("1s").agg(
        {"mid": ["first", "max", "min", "last"], "volume": "sum"}).dropna()
    bars.columns = ["open", "high", "low", "close", "volume"]
    return bars


def main() -> int:
    store = FeatureStore()
    args = sys.argv[1:]
    if len(args) == 2:
        start = datetime.fromisoformat(args[0]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(args[1]).replace(tzinfo=timezone.utc)
    else:
        end = datetime.now(timezone.utc) - timedelta(hours=2)  # dukascopy lags ~1-2h
        start = end - timedelta(days=3)
    hours = int((end - start).total_seconds() // 3600)
    print(f"[TICKS] XAUUSD {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} ({hours}h)")
    ticks = fetch_range(start, end)
    if ticks.empty:
        print("[TICKS] no tick data retrieved (network/block?) - nothing written")
        return 1
    spread = (ticks["ask"] - ticks["bid"])
    print(f"[TICKS] decoded {len(ticks):,} ticks | "
          f"avg spread ${spread.mean():.3f} | median ${spread.median():.3f}")
    sec = ticks_to_seconds(ticks)
    store.write_table("ticks", ticks.head(2_000_000))
    store.write_bars("1m", sec.resample("1min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna())
    store.write_table("tick_meta", pd.DataFrame([{
        "time": pd.Timestamp.utcnow().tz_convert("UTC"),
        "from": ticks.index[0], "to": ticks.index[-1],
        "ticks": len(ticks), "avg_spread": float(spread.mean()),
    }]))
    store.close()
    print(f"[TICKS] landed in store: ticks + 1m bars ({len(sec):,} sec-bars aggregated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
