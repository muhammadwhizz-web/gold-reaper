"""
GOLD REAPER APEX :: News & Economic-Calendar Pipeline
=====================================================
Ingests the live ForexFactory weekly calendar (public JSON mirror),
normalizes to UTC, scores gold relevance, and lands it in the store.

The feed is refreshed by the bot every 15 minutes while running; the last
successful snapshot is cached to data/news/calendar_latest.json so the
news brain keeps working through network hiccups.

Run:  python data/news_ingest.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.store import FeatureStore  # noqa: E402

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE = ROOT / "data" / "news" / "calendar_latest.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) GoldReaperApex/2.0"}

# events that move gold through USD rates / risk sentiment
GOLD_KEYWORDS = {
    "cpi": 1.0, "inflation": 0.9, "core retail": 0.8, "non-farm": 1.0,
    "nonfarm": 1.0, "nfp": 1.0, "fomc": 1.0, "federal funds": 1.0,
    "interest rate": 0.95, "pce": 0.9, "ppi": 0.75, "gdp": 0.7,
    "unemployment": 0.85, "jobless": 0.7, "powell": 0.9, "yellen": 0.6,
    "treasury": 0.6, "ism": 0.6, "pmi": 0.6, "consumer sentiment": 0.5,
    "crude": 0.4, "jolts": 0.6, "adp": 0.6, "claim": 0.5,
}


def fetch_calendar() -> list[dict]:
    req = urllib.request.Request(FEED, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = json.loads(r.read().decode("utf-8", errors="replace"))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                                 "events": raw}, ensure_ascii=False))
    return raw


def load_calendar() -> list[dict]:
    """Live fetch with disk-cache fallback - never dies offline."""
    try:
        return fetch_calendar()
    except Exception as e:  # noqa: BLE001
        print(f"[NEWS] live feed failed ({e}) - using cached snapshot")
        if CACHE.exists():
            return json.loads(CACHE.read_text()).get("events", [])
        return []


def gold_relevance(title: str, country: str) -> float:
    t = (title or "").lower()
    score = 0.0
    for kw, w in GOLD_KEYWORDS.items():
        if kw in t:
            score = max(score, w)
    if country == "USD" and score == 0.0:
        score = 0.35          # any US print moves gold
    if country in ("EUR", "GBP", "JPY", "CNY") and score == 0.0:
        score = 0.15
    return score


def normalize(events: list[dict]) -> pd.DataFrame:
    rows = []
    for e in events:
        try:
            dt = pd.to_datetime(e.get("date"), utc=True, format="mixed")
        except Exception:  # noqa: BLE001
            continue
        impact = (e.get("impact") or "Low").title()
        title = e.get("title") or ""
        country = e.get("country") or ""
        rel = gold_relevance(title, country)
        rows.append({
            "time": dt,
            "country": country,
            "title": title,
            "impact": impact,
            "forecast": e.get("forecast") or "",
            "previous": e.get("previous") or "",
            "gold_relevance": rel,
            "is_high": impact == "High",
            "is_usd": country == "USD",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(subset=["time", "country", "title"]).sort_values("time")


def main() -> int:
    events = load_calendar()
    df = normalize(events)
    print("=" * 66)
    print(" GOLD REAPER APEX :: economic calendar ingestion")
    print("=" * 66)
    if df.empty:
        print(" [!] no events parsed - calendar empty")
        return 1
    high = df[df["is_high"] & (df["gold_relevance"] >= 0.6)]
    print(f" events: {len(df)} total | {len(df[df['is_high']])} high-impact "
          f"| {len(high)} gold-critical")
    now = pd.Timestamp.utcnow().tz_convert("UTC")
    upcoming = high[high["time"] >= now].head(8)
    print(" next gold-critical windows (UTC):")
    for _, r in upcoming.iterrows():
        print(f"   {r['time']:%Y-%m-%d %H:%M}  [{r['country']}] {r['title']} "
              f"(rel {r['gold_relevance']:.2f})")
    store = FeatureStore()
    store.write_table("calendar", df, replace=True)
    store.close()
    print(f" calendar table updated ({len(df)} rows) + cached to {CACHE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
