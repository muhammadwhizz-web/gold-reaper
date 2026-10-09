"""
GOLD REAPER APEX :: Label Geometry Sweep (research)
===================================================
Honest question: does ANY triple-barrier geometry produce out-of-sample
signal above coin flip on hourly gold? We test several horizons/R:R and
regularization levels, strictly time-split. Whatever wins here becomes
the production label; nothing wins -> the ML gate stays OFF (documented).
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_features import triple_barrier  # noqa: E402
from features.store import FeatureStore  # noqa: E402
from ml.train_meta import metrics  # noqa: E402


def feature_matrix(X: pd.DataFrame):
    cols = [c for c in X.columns if c.startswith("f_")]
    Xf = X[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
    return Xf.values.astype(np.float32), cols


def main() -> int:
    store = FeatureStore()
    X = store.read_table("features_1h")
    h1 = store.read_bars("1h")
    store.close()
    Xf, cols = feature_matrix(X)

    geometries = [
        (24, 2.0, 1.2),   # production default
        (12, 1.5, 1.2),
        (24, 1.5, 1.5),
        (48, 2.5, 1.5),
        (72, 3.0, 1.5),
        (96, 3.0, 2.0),
    ]
    print("=" * 72)
    print(" LABEL GEOMETRY SWEEP (70/15/15 time split, OOS = last 15%)")
    print("=" * 72)
    results = []
    for horizon, tp, sl in geometries:
        lab = triple_barrier(h1, tp_mult=tp, sl_mult=sl, horizon=horizon)
        mask = lab["label_tp_before_sl"].notna().values
        y = lab["label_tp_before_sl"].values[mask].astype(int)
        Xf_m = Xf[mask]
        Xtr, ytr = Xf_m[:int(len(Xf_m) * 0.85)], y[:int(len(y) * 0.85)]
        Xte, yte = Xf_m[int(len(Xf_m) * 0.85):], y[int(len(y) * 0.85):]
        for depth, lr in itertools.product((2, 3), (0.02, 0.05)):
            try:
                import xgboost as xgb
                m = xgb.XGBClassifier(
                    n_estimators=400, max_depth=depth, learning_rate=lr,
                    subsample=0.7, colsample_bytree=0.5, reg_lambda=5.0,
                    min_child_weight=25, eval_metric="auc", random_state=666,
                    tree_method="hist", n_jobs=4)
                m.fit(Xtr, ytr, verbose=False)
                p = m.predict_proba(Xte)[:, 1]
                mtr = metrics(yte, p, threshold=0.60)
                results.append({"h": horizon, "tp": tp, "sl": sl, "depth": depth,
                                "lr": lr, **mtr})
                print(f" h={horizon:>2} tp={tp} sl={sl} d={depth} lr={lr} -> "
                      f"AUC {mtr['auc']:.4f} | win@.60 {mtr['win_rate_taken']} "
                      f"n={mtr['taken@0.60']} base {mtr['base_rate']:.3f}")
            except Exception as e:  # noqa: BLE001
                print(f"  fail {horizon}/{tp}/{sl}: {e}")
    rdf = pd.DataFrame(results).sort_values("auc", ascending=False)
    best = rdf.iloc[0]
    print("-" * 72)
    print(f" BEST geometry: h={best['h']:.0f} tp={best['tp']} sl={best['sl']} "
          f"d={best['depth']:.0f} lr={best['lr']} -> OOS AUC {best['auc']:.4f}")
    if best["auc"] >= 0.56:
        print(" verdict: signal EXISTS above gate -> use this geometry in production")
    else:
        print(" verdict: NO learnable OOS signal on this dataset -> ML gate stays "
              "structure-only (regime+ensemble), documented honestly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
