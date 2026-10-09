"""
GOLD REAPER APEX :: Daily-Domain ML Probe (research)
====================================================
Hourly tabular ML showed no OOS edge (sweep_labels.py verdict: 0.50 AUC).
Last honest attempt in the ML dimension: train on 20 YEARS of daily bars -
more history, cleaner signal-to-noise, regime/seasonality structure.

If this passes AUC >= 0.56 the meta-model becomes a DAILY BIAS CONDITIONER
(gates direction confidence). If not, the ML gate runs structure-only and
that is documented as a finding, not hidden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_features import build_features, triple_barrier  # noqa: E402
from features.store import FeatureStore  # noqa: E402
from ml.train_meta import metrics  # noqa: E402


def main() -> int:
    store = FeatureStore()
    d1 = store.read_bars("1d")
    store.close()
    if len(d1) < 2500:
        print("[!] need daily bars - run ingest_multi_tf.py")
        return 1
    print(f" building D1 features over {len(d1):,} daily bars "
          f"({d1.index[0].date()} .. {d1.index[-1].date()})")
    X = build_features(d1, None, with_labels=False, verbose=False)
    lab = triple_barrier(d1, tp_mult=2.5, sl_mult=1.5, horizon=10)
    X["label_tp_before_sl"] = lab["label_tp_before_sl"]
    X = X.dropna(subset=["label_tp_before_sl"])
    cols = [c for c in X.columns if c.startswith("f_")]
    Xf = X[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0).values.astype(np.float32)
    y = X["label_tp_before_sl"].astype(int).values
    print(f" dataset {len(y):,} rows | {len(cols)} features | base rate {y.mean():.3f}")

    cut = int(len(y) * 0.80)
    import xgboost as xgb
    best = None
    for depth, lr, mcw in ((2, 0.02, 30), (3, 0.03, 20), (2, 0.05, 40), (3, 0.02, 50)):
        m = xgb.XGBClassifier(n_estimators=500, max_depth=depth, learning_rate=lr,
                              subsample=0.75, colsample_bytree=0.6,
                              reg_lambda=6.0, min_child_weight=mcw,
                              eval_metric="auc", random_state=666,
                              tree_method="hist", n_jobs=4)
        m.fit(Xf[:cut], y[:cut], verbose=False)
        p = m.predict_proba(Xf[cut:])[:, 1]
        mtr = metrics(y[cut:], p, threshold=0.60)
        print(f" d={depth} lr={lr} mcw={mcw} -> OOS AUC {mtr['auc']:.4f} "
              f"win@.60 {mtr['win_rate_taken']} n={mtr['taken@0.65']}")
        if best is None or mtr["auc"] > best[0]:
            best = (mtr["auc"], depth, lr, mcw)
    auc_best = best[0]
    print("-" * 66)
    if auc_best >= 0.56:
        print(f" VERDICT: DAILY ML HAS EDGE (AUC {auc_best:.3f}) -> "
              "meta-model = daily bias conditioner")
        return 0
    print(f" VERDICT: no daily edge either (AUC {auc_best:.3f}) -> "
          "ML gate stays structure-only; documented as an honest finding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
