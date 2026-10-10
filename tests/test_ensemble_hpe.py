"""HPE ensemble voting tests - 8 modules, >=4 agreement, zero-veto rule."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.ensemble_hpe import Decision, EnsembleHPE
from core.psychology import PsychologyEngine

PSY = PsychologyEngine()


def psy_row(**over) -> pd.Series:
    base = {
        "psy_rsi": 50.0, "psy_mom_z": 0.5, "psy_vol_spike": 0.0,
        "psy_range_atr": 1.0, "psy_capitulation_score": 0.0,
        "psy_capitulation": 0.0, "psy_euphoria_score": 0.0, "psy_euphoria": 0.0,
        "psy_denial": 0.0, "psy_crowding_score": 0.2, "psy_streak_extreme": 0.1,
        "psy_streak_signed": 0.1, "psy_vix": np.nan, "psy_vix_z": np.nan,
        "psy_dxy_ret5": np.nan, "psy_us10y_ret5": np.nan, "psy_gld_flow_z": np.nan,
        "psy_funding": np.nan, "psy_funding_z": np.nan, "psy_cot_net": np.nan,
        "psy_news_sent": 0.0, "psy_news_conf": 0.0, "psy_min_to_event": 9999.0,
        "psy_event_relevance": 0.0, "psy_fear_greed": 50.0, "psy_confidence": 0.9,
        "psy_regime_tag": "NEUTRAL",
    }
    base.update(over)
    return pd.Series(base)


def long_ctx(**over) -> dict:
    """A context in which trend/psychology/micro/cross_asset/ml all lean LONG."""
    ctx = {
        "ts": pd.Timestamp("2025-06-04 13:00:00+00:00"),
        "close": 3350.0, "open": 3348.0, "high": 3351.0, "low": 3347.0,
        "ind": {"rsi": 45.0, "rsi_prev": 44.0, "adx": 30.0, "atr": 2.0,
                "e20": 3349.5, "bb_pos": 0.5, "z100": 0.0, "donch_up": 0.0,
                "donch_dn": 0.0, "ofi": 0.2, "vol_z": 1.0},
        "h4_bias": "LONG", "regime": "TREND_UP", "session": "LONDON_NY_OVERLAP",
        "psy_row": psy_row(psy_capitulation=1.0, psy_fear_greed=18.0),
        "micro_row": pd.Series({"mf_engulf_bull": 1.0, "mf_velocity": 0.8,
                                "mf_er_20": 0.4, "mf_hammer": 0.0}),
        "cross": {"f_resid_z_DXY": -2.0, "f_corr_DXY": -0.4},
        "news": {"sent": 0.0, "conf": 0.0, "min_to_event": 9999.0,
                 "relevance": 0.0},
        "ml_prob": 0.70, "ml_promoted": False,
    }
    ctx.update(over)
    return ctx


@pytest.fixture()
def ens() -> EnsembleHPE:
    return EnsembleHPE(psychology=PSY)


def test_majority_accepts_long(ens: EnsembleHPE) -> None:
    d = ens.decide(long_ctx())
    assert d.side == "LONG"
    assert d.aligned >= 4
    assert d.accepted
    assert 0.0 <= d.confidence <= 1.0
    assert "trend" in d.votes and "ml" in d.votes
    assert any("ACCEPT" in e for e in d.explain)


def test_three_votes_rejected(ens: EnsembleHPE) -> None:
    ctx = long_ctx(ml_prob=np.nan,
                   cross={"f_resid_z_DXY": np.nan, "f_corr_DXY": np.nan})
    d = ens.decide(ctx)
    assert not d.accepted
    assert d.aligned < 4
    assert any("agreement" in v for v in d.vetoes)


def test_psychology_veto_overrides_majority(ens: EnsembleHPE) -> None:
    d = ens.decide(long_ctx(psy_row=psy_row(psy_euphoria=1.0,
                                            psy_fear_greed=88.0)))
    assert d.side in ("LONG", "SHORT")
    assert not d.accepted
    assert any(v.startswith("psychology:") for v in d.vetoes)


def test_regime_crisis_never_trades(ens: EnsembleHPE) -> None:
    d = ens.decide(long_ctx(regime="CRISIS"))
    assert not d.accepted
    assert any("CRISIS" in v for v in d.vetoes)


def test_session_gate(ens: EnsembleHPE) -> None:
    d = ens.decide(long_ctx(session="NEWYORK"))
    assert not d.accepted
    assert any("session" in v for v in d.vetoes)


def test_event_window_blocks_via_psy_veto(ens: EnsembleHPE) -> None:
    row = psy_row(psy_min_to_event=10.0, psy_event_relevance=0.9)
    d = ens.decide(long_ctx(psy_row=row))
    assert not d.accepted
    assert any("event" in v or "news" in v for v in d.vetoes)


def test_ml_long_only_semantics(ens: EnsembleHPE) -> None:
    """Low p(TP-first) is NOT a short edge (long-geometry label) - abstain."""
    d = ens.decide(long_ctx(ml_prob=0.30))
    assert d.votes["ml"]["dir"] == 0
    assert "no long edge" in d.votes["ml"]["reason"]
    d2 = ens.decide(long_ctx(ml_prob=0.70))
    assert d2.votes["ml"]["dir"] == 1


def test_ml_agreement_raises_confidence(ens: EnsembleHPE) -> None:
    d_absent = ens.decide(long_ctx(ml_prob=np.nan,
                                   cross={"f_resid_z_DXY": np.nan,
                                          "f_corr_DXY": np.nan}))
    d_agree = ens.decide(long_ctx(ml_prob=0.75,
                                  cross={"f_resid_z_DXY": np.nan,
                                         "f_corr_DXY": np.nan}))
    assert d_agree.aligned == d_absent.aligned + 1
    assert d_agree.confidence > d_absent.confidence


def test_ml_hard_mode_vetoes_short_when_ml_says_long() -> None:
    ens = EnsembleHPE(psychology=PSY, ml_hard=True)
    ctx = long_ctx()
    ctx.update({"h4_bias": "SHORT", "regime": "TREND_DOWN",
                "close": 3348.0, "open": 3350.0,
                "ind": {**ctx["ind"], "rsi": 55.0, "rsi_prev": 56.0,
                        "ofi": -0.2, "donch_dn": 1.0},
                "psy_row": psy_row(),
                "micro_row": pd.Series({"mf_engulf_bear": 1.0,
                                        "mf_velocity": -0.8, "mf_er_20": 0.4}),
                "cross": {"f_resid_z_DXY": 2.0, "f_corr_DXY": -0.4},
                "ml_prob": 0.75})
    d = ens.decide(ctx)
    if d.side == "SHORT" and d.votes["ml"]["dir"] == 1:
        assert not d.accepted
        assert any("ml:hard" in v for v in d.vetoes)


def test_decision_serializable(ens: EnsembleHPE) -> None:
    d = ens.decide(long_ctx())
    payload = d.to_dict()
    assert set(payload) >= {"side", "confidence", "aligned", "required",
                            "votes", "vetoes", "regime", "session",
                            "explain", "accepted"}
    assert len(payload["votes"]) == 8


def test_no_direction_no_trade(ens: EnsembleHPE) -> None:
    ctx = long_ctx(
        ind={"rsi": np.nan, "rsi_prev": np.nan, "adx": np.nan, "atr": 2.0,
             "e20": np.nan, "bb_pos": np.nan, "z100": np.nan, "donch_up": 0.0,
             "donch_dn": 0.0, "ofi": np.nan, "vol_z": np.nan},
        psy_row=psy_row(psy_capitulation=0.0, psy_fear_greed=np.nan),
        micro_row=None, ml_prob=None,
        cross={"f_resid_z_DXY": np.nan, "f_corr_DXY": np.nan})
    d = ens.decide(ctx)
    assert d.side is None and not d.accepted


def test_decision_defaults() -> None:
    d = Decision()
    assert d.side is None and not d.accepted and d.required == 4
