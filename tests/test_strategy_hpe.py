"""HPE strategy tests - geometry, gates, and the exit state machine."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.ensemble_hpe import Decision
from core.strategy_hpe import HPEConfig, StrategyHPE


def synth_h1(n: int = 320, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1.2, n)
    close = 3300 + np.cumsum(steps)
    open_ = close + rng.normal(0, 0.4, n)
    high = np.maximum(open_, close) + rng.random(n) * 1.5 + 0.2
    low = np.minimum(open_, close) - rng.random(n) * 1.5 - 0.2
    vol = rng.integers(800, 4000, n).astype(float)
    idx = pd.date_range("2025-05-02 00:00", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def synth_h4(n: int = 80, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = np.abs(rng.normal(2.0, 1.0, n))          # persistent up-drift
    close = 3200 + np.cumsum(steps)
    open_ = close - steps
    high = close + 1.0
    low = open_ - 1.0
    idx = pd.date_range("2025-04-01 00:00", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": 1000.0}, index=idx)


class StubEnsemble:
    """Injectable ensemble returning a fixed accepted decision."""

    def __init__(self, side: str = "LONG", accepted: bool = True) -> None:
        self.side = side
        self.accepted = accepted

    def decide(self, ctx: dict) -> Decision:
        d = Decision(side=self.side, confidence=0.72, aligned=5, required=4,
                     regime=str(ctx.get("regime")), session=str(ctx.get("session")),
                     explain=["stub"], accepted=self.accepted)
        d.votes = {m: {"dir": 1 if self.side == "LONG" else -1,
                       "strength": 0.5, "reason": "stub"} for m in
                   ("trend", "psychology", "micro", "cross_asset", "ml")}
        return d


def ts_at(hour: int, weekday: int = 2) -> datetime:
    return datetime(2025, 6, 2 + weekday, hour, 0, tzinfo=timezone.utc)


def test_geometry_long() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("LONG"))
    h1, h4 = synth_h1(), synth_h4()
    sig = s.evaluate(h1, h4, ts_at(13))
    assert sig is not None and sig.side == "LONG"
    sl_dist = sig.entry - sig.sl
    atr_v = sig.atr
    assert sl_dist == pytest.approx(1.2 * atr_v, rel=1e-9)
    assert (sig.tp - sig.entry) / sl_dist == pytest.approx(2.2, rel=1e-9)
    assert 2.0 <= s.cfg.tp_r <= 2.5
    assert sig.meta["votes"] and sig.meta["explain"]
    assert sig.risk_distance > 0


def test_geometry_short_mirrored() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("SHORT"))
    sig = s.evaluate(synth_h1(seed=3), synth_h4(seed=9), ts_at(14))
    assert sig is not None and sig.side == "SHORT"
    assert sig.sl > sig.entry > sig.tp


def test_rejected_decision_returns_none() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("LONG", accepted=False))
    assert s.evaluate(synth_h1(), synth_h4(), ts_at(13)) is None


def test_session_gate_blocks_off_hours() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("LONG"))
    h1, h4 = synth_h1(), synth_h4()
    for hour in (2, 9, 18, 22):          # ASIA / LONDON / NEWYORK / LATE_US
        assert s.evaluate(h1, h4, ts_at(hour)) is None


def test_friday_evening_cutoff() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("LONG"))
    assert s.evaluate(synth_h1(), synth_h4(), ts_at(19, weekday=3)) is None


def test_warmup_guard() -> None:
    s = StrategyHPE(cfg=HPEConfig(), ensemble=StubEnsemble("LONG"))
    assert s.evaluate(synth_h1(120), synth_h4(80), ts_at(13)) is None
    assert s.evaluate(synth_h1(400), synth_h4(40), ts_at(13)) is None


def test_h4_bias_direction() -> None:
    assert StrategyHPE.h4_bias(synth_h4()) == "LONG"


def _pos(side: str = "LONG") -> dict:
    atr_v = 1.0
    entry = 100.0
    sl = entry - 1.2 * atr_v if side == "LONG" else entry + 1.2 * atr_v
    tp = entry + 2.2 * 1.2 if side == "LONG" else entry - 2.2 * 1.2
    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "orig_sl": sl,
            "be_moved": False, "trail_active": False, "atr": atr_v}


def test_exit_sl_hit() -> None:
    s = StrategyHPE(cfg=HPEConfig())
    action, price = s.manage_exit(_pos(), bar_high=100.4, bar_low=98.5, atr_v=1.0)
    assert action == "EXIT_SL" and price == pytest.approx(98.8)


def test_exit_same_bar_conservative_sl_first() -> None:
    s = StrategyHPE(cfg=HPEConfig())
    action, price = s.manage_exit(_pos(), bar_high=102.6, bar_low=98.5, atr_v=1.0)
    assert action == "EXIT_SL" and price == pytest.approx(98.8)


def test_exit_tp_hit() -> None:
    s = StrategyHPE(cfg=HPEConfig())
    action, price = s.manage_exit(_pos(), bar_high=102.7, bar_low=99.5, atr_v=1.0)
    assert action == "EXIT_TP" and price == pytest.approx(102.64, rel=1e-6)


def test_r_dist_uses_orig_sl_distance_not_price() -> None:
    """SD-7 regression guard: after BE, r_mult must use the ORIGINAL risk
    DISTANCE (entry - orig_sl = 1.2), not the orig_sl PRICE (98.8)."""
    s = StrategyHPE(cfg=HPEConfig())
    pos = _pos()
    pos["be_moved"], pos["sl"] = True, 100.05
    # move 1.3 => r_mult 1.083: past BE(1R), short of trail(1.5R) -> HOLD
    assert s.manage_exit(pos, bar_high=101.3, bar_low=100.2, atr_v=1.0)[0] == "HOLD"
    # move 1.85 => r_mult 1.5417 >= 1.5 -> trail engages (dead under SD-7 math)
    assert s.manage_exit(pos, bar_high=101.85, bar_low=100.4, atr_v=1.0)[0] == "TRAIL"


def test_breakeven_move() -> None:
    s = StrategyHPE(cfg=HPEConfig())
    pos = _pos()
    action, price = s.manage_exit(pos, bar_high=101.3, bar_low=99.9, atr_v=1.0)
    assert action == "BE" and price == pytest.approx(100.05)
    pos["be_moved"], pos["sl"] = True, price
    action, _ = s.manage_exit(pos, bar_high=100.6, bar_low=100.1, atr_v=1.0)
    assert action == "HOLD"


def test_trail_engages_after_1_5r() -> None:
    s = StrategyHPE(cfg=HPEConfig())
    pos = _pos()
    pos["be_moved"], pos["sl"] = True, 100.05
    action, price = s.manage_exit(pos, bar_high=101.85, bar_low=100.4, atr_v=1.0)
    assert action == "TRAIL"
    assert price == pytest.approx(101.85 - 1.2, rel=1e-9)
    pos["trail_active"], pos["sl"] = True, price
    action, price2 = s.manage_exit(pos, bar_high=102.4, bar_low=100.9, atr_v=1.0)
    assert action == "TRAIL" and price2 > price


def test_config_env_band_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HPE_TP_R", "4.0")          # out of band -> clipped
    cfg = HPEConfig.from_env()
    assert 2.0 <= cfg.tp_r <= 2.5
    monkeypatch.setenv("HPE_MIN_AGREEMENT", "1")   # floor at 2
    assert HPEConfig.from_env().min_agreement >= 2
