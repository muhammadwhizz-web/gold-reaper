"""
GOLD REAPER APEX :: News Brain — event risk + sentiment scoring
===============================================================
Fuses two live dimensions into the strategy:

  1. EVENT RISK   - is a gold-critical calendar bomb about to drop?
                    (reads the `calendar` table populated by data/news_ingest.py)
  2. SENTIMENT    - lexicon-based gold-specific headline scorer, with an
                    optional LLM endpoint (APEX_LLM_URL) for transformer-grade
                    scoring when the user configures one.

Scores: -1.0 (max bearish gold) .. +1.0 (max bullish gold)
Confidence: keyword coverage 0..1   |  horizon: minutes
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ lexicon
# weight x direction for gold. hawkish USD data = bearish gold, etc.
BULL_GOLD = {
    "dovish": 0.8, "rate cut": 0.9, "cuts rates": 0.95, "stimulus": 0.7,
    "inflation surge": 0.8, "hot inflation": 0.8, "cpi above": 0.7,
    "escalation": 0.7, "war": 0.8, "sanction": 0.6, "conflict": 0.7,
    "safe haven": 0.9, "central bank buying": 0.9, "gold purchases": 0.85,
    "de-dollarization": 0.8, "bank failure": 0.8, "recession fear": 0.6,
    "fed pause": 0.6, "yield drop": 0.7, "dollar weakens": 0.8,
    "geopolitical tension": 0.7, "missile": 0.7, "strike": 0.4,
    "defaults": 0.6, "debt ceiling": 0.5, "tariff": 0.4,
}
BEAR_GOLD = {
    "hawkish": 0.8, "rate hike": 0.9, "hikes rates": 0.95, "higher for longer": 0.8,
    "strong dollar": 0.7, "dollar strengthens": 0.8, "yield rise": 0.7,
    "inflation cooling": 0.6, "cpi below": 0.7, "peace deal": 0.7,
    "ceasefire": 0.8, "de-escalation": 0.7, "risk appetite": 0.5,
    "gold selling": 0.6, "etf outflows": 0.7, "profit taking": 0.4,
    "strong jobs": 0.6, "beat expectations": 0.4, "risk-on": 0.5,
    "fed cut bets pared": 0.6, "real yields up": 0.8,
}


@dataclass
class NewsState:
    risk_window: bool = False          # inside a high-impact event ± buffer?
    minutes_to_event: float = 9999.0
    next_event: str = ""
    next_event_relevance: float = 0.0
    minutes_since_event: float = 9999.0
    last_event: str = ""
    last_event_relevance: float = 0.0
    sentiment: float = 0.0             # -1..+1
    sentiment_confidence: float = 0.0
    horizon_minutes: float = 240.0
    engine: str = "lexicon"
    trace: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"risk_window": self.risk_window,
                "min_to_event": round(self.minutes_to_event, 1),
                "next_event": self.next_event[:60],
                "relevance": round(self.next_event_relevance, 2),
                "min_since_event": round(self.minutes_since_event, 1),
                "last_event": self.last_event[:60],
                "sentiment": round(self.sentiment, 2),
                "confidence": round(self.sentiment_confidence, 2),
                "engine": self.engine}


class NewsBrain:
    name = "APEX-NEWS"

    def __init__(self, store, cfg=None) -> None:
        self.store = store
        self.cfg = cfg
        self._calendar: pd.DataFrame | None = None
        self._loaded_at: datetime | None = None

    # ------------------------------------------------------------ calendar
    def _load_calendar(self, max_age_min: int = 30) -> pd.DataFrame | None:
        """Refresh calendar from store at most every 30 min."""
        import core.sessions as S
        now = S.now_utc()
        if (self._calendar is not None and self._loaded_at is not None
                and (now - self._loaded_at).total_seconds() < max_age_min * 60):
            return self._calendar
        try:
            cal = self.store.read_table("calendar")
            if cal.empty:
                return self._calendar
            cal = cal.reset_index() if "time" not in cal.columns else cal
            self._calendar = cal
            self._loaded_at = now
        except Exception:  # noqa: BLE001
            pass
        return self._calendar

    # ------------------------------------------------------------ event risk
    def event_risk(self, now: pd.Timestamp, buffer_before: int = 45,
                   buffer_after: int = 30) -> NewsState:
        st = NewsState()
        cal = self._load_calendar()
        if cal is None or cal.empty:
            return st
        hi = cal[(cal["is_high"] == True) &  # noqa: E712
                 (cal["gold_relevance"] >= 0.6)]
        if hi.empty:
            return st
        times = pd.to_datetime(hi["time"], utc=True).sort_values()
        # next upcoming (or just-passed within after-buffer)
        nxt = times[times >= now - timedelta(minutes=buffer_after)]
        if not nxt.empty:
            t0 = nxt.iloc[0]
            ev = hi.set_index("time").loc[t0] if t0 in hi.set_index("time").index \
                else None
            delta_min = (t0 - now).total_seconds() / 60.0
            st.minutes_to_event = delta_min
            if ev is not None:
                st.next_event = str(ev.get("title", ""))
                st.next_event_relevance = float(ev.get("gold_relevance", 0))
            if -buffer_after <= delta_min <= buffer_before:
                st.risk_window = True
                st.trace.append(f"risk window: {st.next_event} in {delta_min:.0f} min")
        # last passed event (for post-news momentum module)
        past = times[times < now]
        if len(past):
            t_last = past.iloc[-1]
            since = (now - t_last).total_seconds() / 60.0
            idx = hi.set_index("time")
            if t_last in idx.index:
                evl = idx.loc[t_last]
                st.minutes_since_event = since
                st.last_event = str(evl.get("title", ""))
                st.last_event_relevance = float(evl.get("gold_relevance", 0))
        return st

    # ------------------------------------------------------------ sentiment
    def _llm_score(self, headlines: list[str]) -> tuple[float, float] | None:
        url = os.getenv("APEX_LLM_URL", "")
        if not url or not headlines:
            return None
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps({"headlines": headlines[:20]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                out = json.loads(r.read())
            s = float(out.get("score", 0))
            c = float(out.get("confidence", 0))
            return max(-1.0, min(1.0, s)), max(0.0, min(1.0, c))
        except Exception:  # noqa: BLE001
            return None

    def score_headlines(self, headlines: list[str]) -> tuple[float, float, str]:
        """Returns (score -1..1, confidence 0..1, engine)."""
        llm = self._llm_score(headlines)
        if llm is not None:
            return llm[0], llm[1], "llm"
        bull = bear = 0.0
        hits = 0
        for h in headlines:
            t = h.lower()
            for kw, w in BULL_GOLD.items():
                if kw in t:
                    bull = max(bull, w)
                    hits += 1
            for kw, w in BEAR_GOLD.items():
                if kw in t:
                    bear = max(bear, w)
                    hits += 1
        if hits == 0:
            return 0.0, 0.0, "lexicon"
        score = bull - bear
        conf = min(1.0, hits / 6.0)
        return score, conf, "lexicon"

    # ------------------------------------------------------------ composite
    def state(self, now: pd.Timestamp, headlines: list[str] | None = None) -> NewsState:
        st = self.event_risk(now)
        if headlines:
            s, c, eng = self.score_headlines(headlines)
            st.sentiment, st.sentiment_confidence, st.engine = s, c, eng
            st.trace.append(f"sentiment {s:+.2f} (conf {c:.2f}, {eng})")
        return st
