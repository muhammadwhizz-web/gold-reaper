"""
GOLD REAPER :: Session & Time Utilities
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def session_of(ts: datetime | pd.Timestamp) -> str:
    """Map a UTC timestamp to its trading session."""
    h = ts.hour if isinstance(ts, datetime) else ts.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 16:
        return "LONDON_NY_OVERLAP"
    if 16 <= h < 21:
        return "NEWYORK"
    return "LATE_US"


def in_blackout(ts: datetime, windows: tuple) -> bool:
    """windows: tuple of (hour, minute) blackout start moments (10 min span)."""
    for h, m in windows:
        start = ts.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= ts < start + timedelta(minutes=10):
            return True
    return False


def is_market_open(ts: datetime | None = None) -> bool:
    """Rough gold market clock: closed from Fri 21:00 UTC to Sun 22:00 UTC."""
    ts = ts or now_utc()
    if ts.weekday() == 5:  # Saturday
        return False
    if ts.weekday() == 6 and ts.hour < 22:  # Sunday before open
        return False
    if ts.weekday() == 4 and ts.hour >= 21:  # Friday after close
        return False
    return True


def friday_cutoff_reached(ts: datetime, last_entry_hour: int) -> bool:
    return ts.weekday() == 4 and ts.hour >= last_entry_hour


def minutes_until_market_close(ts: datetime) -> float:
    """Minutes until the upcoming weekend close (Fri 21:00 UTC)."""
    if ts.weekday() < 4:
        return 9999.0
    close = ts.replace(hour=21, minute=0, second=0, microsecond=0)
    if ts.weekday() == 4 and ts < close:
        return (close - ts).total_seconds() / 60.0
    return 0.0


def human_local(ts: datetime | None = None, tz_name: str = "UTC") -> str:
    ts = ts or now_utc()
    try:
        return ts.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:  # noqa: BLE001
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
