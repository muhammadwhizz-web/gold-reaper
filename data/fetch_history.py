"""
GOLD REAPER :: Historical XAU/USD Data Hunter
=============================================
Fetches gold price history from multiple free sources:
  - Yahoo Finance: 20y of D1 bars + max-available H1 bars (~2y)
  - Stooq fallback: full daily XAUUSD history
Validates, resamples and stores clean datasets under data/history/.

Works on Linux + Windows. No API key required.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HIST_DIR = ROOT / "data" / "history"
HIST_DIR.mkdir(parents=True, exist_ok=True)

YEARS = 20
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) GoldReaper/1.0"}


def _log(msg: str) -> None:
    print(f"[HUNTER] {msg}", flush=True)


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols].dropna(subset=["close"])
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    return df


def yahoo(symbol: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _norm(df)
    if len(df) == 0:
        return pd.DataFrame()
    _log(f"yahoo {symbol} {interval}: {len(df):,} bars "
         f"({df.index[0].date()} .. {df.index[-1].date()})")
    return df


def stooq_daily() -> pd.DataFrame:
    url = "https://stooq.com/q/d/l/?s=xauusd&i=d"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            import io
            df = pd.read_csv(io.BytesIO(r.read()))
        if df.empty or "Close" not in df.columns:
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        df["time"] = pd.to_datetime(df["date"], utc=True, format="mixed", dayfirst=True)
        df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
        df = df.dropna(subset=["close"])
        _log(f"stooq daily: {len(df):,} bars ({df.index[0].date()} .. {df.index[-1].date()})")
        return df
    except Exception as e:  # noqa: BLE001
        _log(f"stooq failed: {e}")
        return pd.DataFrame()


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "open": df["open"].resample(rule).first(),
        "high": df["high"].resample(rule).max(),
        "low": df["low"].resample(rule).min(),
        "close": df["close"].resample(rule).last(),
        "volume": df["volume"].resample(rule).sum(),
    })
    return out.dropna(subset=["close"])


def session_tag(ts: pd.Timestamp) -> str:
    h = ts.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 16:
        return "LONDON_NY_OVERLAP"
    if 16 <= h < 21:
        return "NEWYORK"
    return "LATE_US"


def analyze(hourly: pd.DataFrame, daily: pd.DataFrame) -> None:
    print("\n" + "=" * 66)
    print(" GOLD REAPER :: XAU/USD DEEP RECON REPORT")
    print("=" * 66)
    print(f" daily window  : {daily.index[0].date()} -> {daily.index[-1].date()} "
          f"({len(daily):,} bars)")
    print(f" hourly window : {hourly.index[0].date()} -> {hourly.index[-1].date()} "
          f"({len(hourly):,} bars)")
    px0, px1 = float(daily["close"].iloc[0]), float(daily["close"].iloc[-1])
    yrs = max((daily.index[-1] - daily.index[0]).days / 365.25, 0.1)
    print(f" gold price    : ${px0:,.0f} -> ${px1:,.0f}  "
          f"(x{px1 / px0:.2f}, {((px1 / px0) ** (1 / yrs) - 1) * 100:.1f}%/yr CAGR)")

    ret = daily["close"].pct_change().dropna()
    print(f" daily vol     : {ret.std() * 100:.2f}%/day  "
          f"(ann. {ret.std() * (252 ** 0.5) * 100:.1f}%)")
    up = (ret > 0).mean()
    print(f" up-day rate   : {up * 100:.1f}%")

    if len(hourly) > 2000:
        hours = hourly.assign(sess=[session_tag(t) for t in hourly.index])
        hours["rng"] = (hours["high"] - hours["low"]) / hours["close"] * 100
        print(" avg hourly range by session (where gold hunts):")
        for s, r in hours.groupby("sess")["rng"].mean().sort_values(ascending=False).items():
            print(f"   {s:<20} {r:.3f}%")
        print(" most explosive UTC hours:")
        for h, r in hours.groupby(hours.index.hour)["rng"].mean().sort_values(
                ascending=False).head(5).items():
            print(f"   {h:02d}:00 UTC     {r:.3f}%")
    print("=" * 66)


def main() -> int:
    daily = yahoo("GC=F", f"{YEARS}y", "1d")
    if daily.empty:
        daily = yahoo("XAUUSD=X", f"{YEARS}y", "1d")
    if daily.empty:
        daily = stooq_daily()
    if daily.empty:
        _log("FATAL: every source failed - check internet")
        return 1
    time.sleep(1.5)

    hourly = yahoo("GC=F", "730d", "1h")
    if hourly.empty:
        hourly = yahoo("XAUUSD=X", "730d", "1h")

    daily = daily[~daily.index.duplicated(keep="last")]
    daily.to_csv(HIST_DIR / "XAUUSD_D1.csv")

    if not hourly.empty:
        hourly = hourly[~hourly.index.duplicated(keep="last")]
        hourly.to_csv(HIST_DIR / "XAUUSD_H1.csv")
        resample(hourly, "4h").to_csv(HIST_DIR / "XAUUSD_H4.csv")

    analyze(hourly if not hourly.empty else daily.resample("1h").ffill().head(0), daily)
    print(f" datasets saved -> {HIST_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
