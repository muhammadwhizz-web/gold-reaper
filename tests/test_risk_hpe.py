"""HPE survival risk layer tests - sizing, brakes, stops, Kelly cap."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.risk_hpe import RiskHPE, RiskHPEConfig

T0 = datetime(2025, 6, 4, 13, 0, tzinfo=timezone.utc)


def ts(h: int = 13, day: int = 4) -> pd.Timestamp:
    return pd.Timestamp(T0.replace(day=day, hour=h))


def make_risk(**over) -> RiskHPE:
    return RiskHPE(cfg=RiskHPEConfig(**over), starting_equity=10_000.0)


# ------------------------------------------------------------------ sizing

def test_confidence_scaling_bounds() -> None:
    r = make_risk()
    lo = r.risk_pct(0.30, "TREND_UP")     # below ramp floor
    mid = r.risk_pct(0.65, "TREND_UP")
    hi = r.risk_pct(0.95, "TREND_UP")
    assert lo == pytest.approx(0.3)
    assert 0.3 < mid < 1.5
    assert hi == pytest.approx(1.5)


def test_regime_scaling_order() -> None:
    r = make_risk()
    conf = 0.8
    trend = r.risk_pct(conf, "TREND_UP")
    rng = r.risk_pct(conf, "RANGE")
    chop = r.risk_pct(conf, "VOLATILE_CHOP")
    crisis = r.risk_pct(conf, "CRISIS")
    assert trend > rng > chop
    assert crisis == 0.0


def test_loss_streak_brake_halves_size() -> None:
    r = make_risk()
    before = r.risk_pct(0.8, "TREND_UP")
    r.record_close(-100.0, T0)
    r.record_close(-120.0, T0)
    assert r.loss_streak == 2
    after = r.risk_pct(0.8, "TREND_UP")
    assert after == pytest.approx(before * 0.5)
    r.record_close(+150.0, T0)            # win resets the brake
    assert r.risk_pct(0.8, "TREND_UP") == pytest.approx(before)


def test_kelly_cap_blocks_negative_edge() -> None:
    r = make_risk()
    for i in range(30):                    # 30% WR, symmetric R -> f* < 0
        r.record_close(+120.0 if i % 10 < 3 else -100.0, T0 + timedelta(hours=i))
    cap = r.kelly_cap_pct()
    assert cap == 0.0
    assert r.risk_pct(0.9, "TREND_UP") == 0.0


def test_kelly_cap_bites_on_thin_edge() -> None:
    r = make_risk()
    for i in range(40):                    # WR ~0.5, R~1.05 -> f* ~0.034
        win = i % 2 == 0
        r.record_close(105.0 if win else -100.0, T0 + timedelta(hours=i))
    cap = r.kelly_cap_pct()
    assert cap is not None and cap < 1.0
    assert r.risk_pct(0.95, "TREND_UP") == pytest.approx(cap, rel=1e-3)


def test_kelly_none_before_min_trades() -> None:
    r = make_risk()
    r.record_close(50.0, T0)
    assert r.kelly_cap_pct() is None


# ------------------------------------------------------------------ stops

def test_daily_stop_blocks() -> None:
    r = make_risk(daily_stop_pct=2.0)
    r.record_close(-150.0, ts(13))
    r.record_close(-80.0, ts(14))
    ok, why = r.allow_entry(ts(15, day=4), equity=10_000.0)
    assert not ok and "daily" in why
    ok2, _ = r.allow_entry(ts(10, day=5), equity=10_000.0)   # next day resets
    assert ok2


def test_weekly_and_monthly_stops() -> None:
    r = make_risk(weekly_stop_pct=5.0)
    # spread losses across 4 days: each day -160 (daily 2% = 200 stays intact)
    for day in (2, 3, 4, 5):               # Mon..Thu, one week
        r.record_close(-160.0, ts(13, day=day))
    ok, why = r.allow_entry(ts(14, day=5), equity=10_000.0)
    assert not ok and "weekly" in why
    r2 = make_risk(monthly_stop_pct=1.0)     # tiny monthly limit to trip fast
    r2.record_close(-150.0, ts(13, day=4))
    ok2, why2 = r2.allow_entry(ts(14, day=4), equity=10_000.0)
    assert not ok2 and "monthly" in why2


def test_session_target_blocks_rest_of_block() -> None:
    r = make_risk(session_target_usd=20.0)
    r.record_close(+25.0, ts(12, day=4))
    ok, why = r.allow_entry(ts(13, day=4), equity=10_000.0)
    assert not ok and "session target" in why
    ok2, _ = r.allow_entry(ts(16, day=4), equity=10_000.0)   # next 4h block
    assert ok2


def test_equity_curve_pause() -> None:
    # daily/weekly/monthly limits effectively disabled -> isolate the curve gate
    r = make_risk(equity_ma_window=20, daily_stop_pct=50.0,
                  weekly_stop_pct=50.0, monthly_stop_pct=50.0)
    for i in range(30):
        pnl = +100.0 if i < 20 else -180.0   # strong run then sharp bleed
        r.record_close(pnl, T0 + timedelta(hours=i))
    equity = r._current_equity()
    ok, why = r.allow_entry(T0 + timedelta(hours=31), equity=equity)
    assert not ok and "equity curve" in why


def test_allow_entry_allows_clean_state() -> None:
    r = make_risk()
    ok, why = r.allow_entry(T0, equity=10_000.0)
    assert ok and why == ""


def test_snapshot_fields() -> None:
    r = make_risk()
    r.record_close(-50.0, T0)
    snap = r.snapshot()
    assert snap["closed_trades"] == 1 and snap["loss_streak"] == 1
    assert snap["kelly_cap_pct"] is None
    assert snap["equity"] == pytest.approx(9_950.0)
