"""
GOLD REAPER APEX :: Meta-Model Trainer (ML gate)
================================================
Trains a gradient-boosted classifier that answers ONE question:

    "Given everything we measured on this bar, what is the probability
     this setup hits TP (2xATR) before SL (1.2xATR) within 24 hours?"

Pipeline:
  - loads features_1h from the DuckDB store (142 features, triple-barrier labels)
  - strict time-series split (no shuffling, no leakage): 70% train / 15% valid / 15% test
  - engine auto-detection: XGBoost -> LightGBM -> sklearn GradientBoosting
  - early stopping on validation AUC
  - PROMOTION GATE: the candidate model replaces production only if its
    OUT-OF-SAMPLE AUC >= 0.56 (above coin flip) and >= previous prod AUC - 0.01
  - production artifact: ml/models/meta_production.joblib + metadata JSON

Run:  python ml/train_meta.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.store import FeatureStore  # noqa: E402
from features.build_features import build_features  # noqa: E402

MODELS_DIR = ROOT / "ml" / "models"
PROD_MODEL = MODELS_DIR / "meta_production.joblib"
PROD_META = MODELS_DIR / "meta_production.json"


# ------------------------------------------------------------------ data


def load_dataset(store: FeatureStore, min_rows: int = 3000) -> pd.DataFrame:
    X = store.read_table("features_1h")
    if len(X) < min_rows:
        h1 = store.read_bars("1h")
        if h1.empty:
            raise RuntimeError("no features and no bars - run ingestion first")
        X = build_features(h1, store)
    X = X.dropna(subset=["label_tp_before_sl"])
    return X


def feature_matrix(X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    cols = [c for c in X.columns if c.startswith("f_")]
    Xf = X[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
    y = X["label_tp_before_sl"].astype(int).values
    return Xf.values.astype(np.float32), y, cols


# ------------------------------------------------------------------ model


class MetaModel:
    """Engine-agnostic wrapper: XGBoost -> LightGBM -> sklearn."""

    def __init__(self) -> None:
        self.engine = "sklearn"
        self.model = None
        self.columns: list[str] = []

    def _make(self, seed: int = 666):
        try:
            import xgboost as xgb
            self.engine = "xgboost"
            return xgb.XGBClassifier(
                n_estimators=600, max_depth=4, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7,
                reg_lambda=2.0, min_child_weight=8,
                eval_metric="auc", random_state=seed,
                tree_method="hist", early_stopping_rounds=60, n_jobs=4)
        except ImportError:
            pass
        try:
            import lightgbm as lgb
            self.engine = "lightgbm"
            return lgb.LGBMClassifier(
                n_estimators=600, num_leaves=24, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7, reg_lambda=2.0,
                min_child_samples=20, random_state=seed, verbosity=-1, n_jobs=4)
        except ImportError:
            pass
        from sklearn.ensemble import GradientBoostingClassifier
        self.engine = "sklearn"
        return GradientBoostingClassifier(
            n_estimators=300, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=seed)

    def train(self, Xtr, ytr, Xva, yva) -> dict:
        model = self._make()
        info: dict = {"engine": self.engine}
        try:
            if self.engine == "xgboost":
                model.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
            elif self.engine == "lightgbm":
                model.fit(Xtr, ytr, eval_set=[(Xva, yva)],
                          callbacks=[])  # early stop via lgb is verbose; keep simple
            else:
                model.fit(Xtr, ytr)
        except TypeError:
            # xgboost <2 without early_stopping_rounds ctor arg
            model.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
        self.model = model
        return info

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            return np.full(len(X), 0.5)
        p = self.model.predict_proba(X)
        return p[:, 1] if p.shape[1] > 1 else p[:, 0]

    def feature_importance(self) -> pd.Series | None:
        if self.model is None:
            return None
        try:
            return pd.Series(self.model.feature_importances_, index=self.columns)
        except Exception:  # noqa: BLE001
            return None


# ------------------------------------------------------------------ metrics


def auc(y: np.ndarray, p: np.ndarray) -> float:
    try:
        from sklearn.metrics import roc_auc_score
        return float(roc_auc_score(y, p))
    except Exception:  # noqa: BLE001
        return 0.5


def metrics(y: np.ndarray, p: np.ndarray, threshold: float = 0.65) -> dict:
    pred = (p >= threshold).astype(int)
    taken = int(pred.sum())
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    return {
        "auc": round(auc(y, p), 4),
        "n": len(y),
        "taken@0.65": taken,
        "win_rate_taken": round(tp / taken, 4) if taken else None,
        "base_rate": round(float(y.mean()), 4),
        "edge_taken": round(tp / taken - y.mean(), 4) if taken else None,
    }


# ------------------------------------------------------------------ main


def main(retrain: bool = True) -> int:
    store = FeatureStore()
    X = load_dataset(store)
    store.close()
    Xtr_end = int(len(X) * 0.70)
    Xva_end = int(len(X) * 0.85)
    Xf, y, cols = feature_matrix(X)
    Xtr, ytr = Xf[:Xtr_end], y[:Xtr_end]
    Xva, yva = Xf[Xtr_end:Xva_end], y[Xtr_end:Xva_end]
    Xte, yte = Xf[Xva_end:], y[Xva_end:]

    print("=" * 66)
    print(" GOLD REAPER APEX :: meta-model training")
    print("=" * 66)
    print(f" dataset      : {len(X):,} labelled bars | {len(cols)} features")
    print(f" base rate    : {y.mean() * 100:.1f}% TP-first (before any model)")
    print(f" splits       : train {len(Xtr):,} / valid {len(Xva):,} / test {len(Xte):,}")

    mm = MetaModel()
    mm.columns = cols
    info = mm.train(Xtr, ytr, Xva, yva)
    p_va = mm.predict_proba(Xva)
    p_te = mm.predict_proba(Xte)
    m_va = metrics(yva, p_va)
    m_te = metrics(yte, p_te)

    print(f" engine       : {mm.engine}")
    print(f" valid        : AUC {m_va['auc']} | win@0.65 {m_va['win_rate_taken']} "
          f"on {m_va['taken@0.65']} signals")
    print(f" test (OOS)   : AUC {m_te['auc']} | win@0.65 {m_te['win_rate_taken']} "
          f"on {m_te['taken@0.65']} signals | base {m_te['base_rate']}")
    imp = mm.feature_importance()
    if imp is not None:
        print(" top features :")
        for name, v in imp.sort_values(ascending=False).head(10).items():
            print(f"   {name:<28} {v:.4f}")

    # ---------------- promotion gate ----------------
    oos_auc = m_te["auc"]
    prev_auc = None
    if PROD_META.exists():
        try:
            prev_auc = json.loads(PROD_META.read_text()).get("test_auc")
        except Exception:  # noqa: BLE001
            pass
    gate = oos_auc >= 0.56 and (prev_auc is None or oos_auc >= prev_auc - 0.01)
    verdict = "PROMOTED to production" if gate else \
        f"REJECTED (needs AUC>=0.56, prev {prev_auc})"
    print(f" gate         : {verdict}")

    if retrain and gate:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        import joblib
        joblib.dump({"model": mm.model, "columns": cols, "engine": mm.engine},
                    PROD_MODEL)
        PROD_META.write_text(json.dumps({
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "engine": mm.engine,
            "rows": int(len(X)),
            "features": len(cols),
            "valid_auc": m_va["auc"],
            "test_auc": m_te["auc"],
            "valid_metrics": m_va,
            "test_metrics": m_te,
            "base_rate": float(y.mean()),
            "gate": "oos_auc>=0.56 and no regression vs previous",
        }, indent=2))
        print(f" artifact     : {PROD_MODEL.name} saved")
    print("=" * 66)
    return 0 if gate else 2


if __name__ == "__main__":
    sys.exit(main())
