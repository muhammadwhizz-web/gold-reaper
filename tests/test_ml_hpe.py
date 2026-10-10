"""HPE ML layer tests - purge/embargo purity, uniqueness, gate, calibration."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.train_hpe import (
    GATE_AUC,
    GATE_WORST_PF,
    evaluate_gate,
    feature_columns,
    fold_pf,
    make_calibrator,
    purged_walkforward,
    uniqueness_weights,
)


def _frame(n: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(index=idx)
    for i in range(8):
        df[f"f_x{i}"] = rng.normal(size=n)
    df["label_hpe_tp_before_sl"] = pd.Series(
        rng.choice([0.0, 1.0], n, p=[0.45, 0.55])).where(
        np.arange(n) < n - 30)
    df["label_hpe_r_multiple"] = np.where(df["label_hpe_tp_before_sl"] == 1,
                                          2.2, -1.0)
    df["_regime"] = "RANGE"
    df["_session"] = "ASIA"
    return df


# ------------------------------------------------------------------ purity

def test_purged_walkforward_no_leakage() -> None:
    X = _frame(1200)
    folds = purged_walkforward(X, folds=4, embargo=24, horizon=24)
    assert len(folds) == 3                      # fold 0 is train-only
    n = len(X)
    bounds = [int(n * k / 4) for k in range(5)]
    y = X["label_hpe_tp_before_sl"]
    event_end = np.where(y.notna().values,
                         np.arange(n) + 24, np.arange(n))
    for k, (tr, te) in enumerate(folds):
        te_lo, te_hi = bounds[k + 1], bounds[k + 2]
        assert te[0] == te_lo and te[-1] == te_hi - 1
        # every kept train event must END before (test_start - embargo)
        assert event_end[tr].max() < te_lo - 24
        # and no train index may sit inside the test span
        assert tr.max() < te_lo


def test_embargo_removes_border_events() -> None:
    X = _frame(800)
    folds = purged_walkforward(X, folds=4, embargo=24, horizon=24)
    tr, te = folds[0]
    # without embargo, train would keep events ending within [te_lo, te_lo+24)
    n = len(X)
    y = X["label_hpe_tp_before_sl"]
    late = np.where(y.notna().values & (np.arange(n) + 24 >= int(n * 0.25) - 24)
                    & (np.arange(n) < int(n * 0.25)))[0]
    if len(late):
        assert not np.isin(late, tr).any()


# --------------------------------------------------------------- weights

def test_uniqueness_weights_overlap_penalised() -> None:
    y = pd.Series([np.nan] * 110)
    y.iloc[0] = 1.0                              # overlapping pair
    y.iloc[1] = 1.0                              # overlaps event 0
    y.iloc[50] = 1.0                             # fully isolated
    y.iloc[100] = 1.0                            # fully isolated
    w = uniqueness_weights(y, horizon=5)
    assert w.iloc[50] == pytest.approx(1.0)      # no concurrency -> full weight
    assert w.iloc[50] == pytest.approx(w.iloc[100])
    assert w.iloc[0] < 1.0 and w.iloc[1] < 1.0   # overlap -> penalised
    assert w.iloc[0] == pytest.approx(w.iloc[1])  # symmetric pair, equal hit


def test_feature_columns_exclude_labels_and_tags() -> None:
    X = _frame()
    cols = feature_columns(X)
    assert all(c.startswith(("f_", "mf_", "psy_")) for c in cols)
    assert not any(c.startswith("label_") for c in cols)
    assert "_regime" not in cols and "_session" not in cols


# ------------------------------------------------------------------ gate

def test_gate_requires_auc_and_worst_fold_pf() -> None:
    good = [{"fold": 1, "auc": 0.62, "pf_taken": 1.5},
            {"fold": 2, "auc": 0.60, "pf_taken": 1.25},
            {"fold": 3, "auc": 0.59, "pf_taken": 1.4}]
    ok, why = evaluate_gate(good)
    assert ok and "worst-fold" in why
    bad_auc = [{"fold": 1, "auc": 0.55, "pf_taken": 1.5}]
    assert not evaluate_gate(bad_auc)[0]
    bad_pf = [{"fold": 1, "auc": 0.62, "pf_taken": 0.9},
              {"fold": 2, "auc": 0.61, "pf_taken": 1.3}]
    assert not evaluate_gate(bad_pf)[0]
    # a fold that took < MIN_TAKEN signals -> pf None -> gate fails
    tiny = [{"fold": 1, "auc": 0.62, "pf_taken": None}]
    assert not evaluate_gate(tiny)[0]
    empty = evaluate_gate([])[0] is False
    assert empty


def test_gate_constants_match_spec() -> None:
    assert GATE_AUC == 0.58 and GATE_WORST_PF == 1.2


# ------------------------------------------------------------ calibration

def test_calibrator_bounds_and_monotonicity() -> None:
    rng = np.random.default_rng(9)
    p = np.clip(rng.normal(0.55, 0.15, 1200), 0, 1)
    y = (rng.random(1200) < p).astype(int)       # well-specified probs
    cal = make_calibrator(1200).fit(p, y)
    out = cal.predict(np.array([0.05, 0.3, 0.5, 0.7, 0.95]))
    assert (out >= 0).all() and (out <= 1).all()
    assert (np.diff(out) >= -1e-9).all()          # monotone non-decreasing


def test_platt_used_for_small_calibration_sets() -> None:
    rng = np.random.default_rng(4)
    p = rng.random(200)
    y = (rng.random(200) < p).astype(int)
    cal = make_calibrator(200)
    assert cal.kind == "platt"
    cal.fit(p, y)
    out = cal.predict(np.array([0.2, 0.8]))
    assert out.shape == (2,) and (out >= 0).all() and (out <= 1).all()


# -------------------------------------------------------------------- pf

def test_fold_pf_math() -> None:
    assert fold_pf(np.array([2.2, -1.0, 2.2, -1.0])) == pytest.approx(2.2)
    assert fold_pf(np.array([2.2, 2.2])) == float("inf")
    assert fold_pf(np.array([-1.0, -1.0])) == 0.0
