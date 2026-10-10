"""
GOLD REAPER HPE :: Meta-Model Trainer (Phase 4)
===============================================
Answers ONE question per bar:

    "Given every measured dimension, what is the probability this setup
     hits the HPE take-profit (2.2R) before the stop (1.2 x ATR) within
     24 hours?"

Pipeline (all leakage-controlled, all reproducible with one command):

  - dataset  : f_* (142 classical) + mf_* (48 micro) + psy_* (27 psychology)
               labels = triple-barrier on HPE geometry (TP 2.64 ATR / SL 1.2 ATR / 24h)
  - weights  : Lopez de Prado average uniqueness x inverse regime frequency
               x inverse session frequency
  - CV       : expanding-window walk-forward, PURGED + EMBARGOED folds
               (train events overlapping the test span are dropped)
  - model    : XGBoost -> LightGBM -> sklearn fallback (engine auto-detect)
               optional temporal-CNN + attention sequence model (torch, opt-in
               via HPE_SEQ=1) - stays a SOFT vote even when it works
  - calibr.  : isotonic (>= 1000 cal rows) else Platt/sigmoid, fit on an inner
               held-out slice of the training span, applied out-of-sample
  - GATE     : model arms ONLY if pooled OOS AUC >= 0.58 AND worst-fold
               PF >= 1.2 on signals taken at prob >= 0.60 (folds with < 5
               taken signals fail the gate - no tiny-sample flukes)
  - artifact : ml/models/hpe_production.joblib + hpe_production.json
               ("armed": false means: soft vote only, never a hard gate)

Losing folds are printed, saved and published. Never hidden.

Run:  python ml/train_hpe.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.psychology import PsychologyEngine  # noqa: E402
from features.build_features import build_features, triple_barrier  # noqa: E402
from features.micro_features import compute_micro_features  # noqa: E402
from features.store import FeatureStore  # noqa: E402

MODELS_DIR = ROOT / "ml" / "models"
ART_MODEL = MODELS_DIR / "hpe_production.joblib"
ART_META = MODELS_DIR / "hpe_production.json"

HPE_TP_R = 2.2
HPE_SL_ATR = 1.2
HORIZON = 24
EMBARGO = 24
FOLDS = 4
PROB_THRESHOLD = 0.60
MIN_TAKEN = 5
GATE_AUC = 0.58
GATE_WORST_PF = 1.2

LABEL_COLS = ("label_tp_before_sl", "label_r_multiple",
              "label_hpe_tp_before_sl", "label_hpe_r_multiple")


# ------------------------------------------------------------------ dataset


def assemble_dataset(store: FeatureStore, verbose: bool = True) -> pd.DataFrame:
    """f_* + mf_* + psy_* + HPE-geometry triple-barrier labels, one frame."""
    X = store.read_table("features_1h")
    h1 = store.read_bars("1h")
    if h1.empty:
        raise RuntimeError("no 1h bars - run ingestion first")
    if X.empty or len(X) != len(h1):
        if verbose:
            print("  [data] building classical features (f_*)...")
        X = build_features(h1, store, with_labels=False, verbose=verbose)
    if verbose:
        print("  [data] micro features (mf_*)...")
    mf = compute_micro_features(h1)
    if verbose:
        print("  [data] psychology frame (psy_*)...")
    psy = PsychologyEngine().compute_frame(h1)
    psy = psy.drop(columns=["psy_regime_tag"])
    X = X.join(mf, how="inner").join(psy, how="inner")

    # HPE-geometry labels: TP = tp_r * sl_atr = 2.64 ATR, SL = 1.2 ATR
    lab = triple_barrier(h1, tp_mult=HPE_TP_R * HPE_SL_ATR,
                         sl_mult=HPE_SL_ATR, horizon=HORIZON)
    X["label_hpe_tp_before_sl"] = lab["label_tp_before_sl"]
    X["label_hpe_r_multiple"] = lab["label_r_multiple"]

    # regime / session tags for weighting (deterministic rules engine)
    from core.regime import RegimeDetector
    X["_regime"] = RegimeDetector().classify_frame(h1).loc[X.index]
    from core.sessions import session_of
    X["_session"] = [session_of(t) for t in X.index]
    return X


def feature_columns(X: pd.DataFrame) -> list[str]:
    skip = set(LABEL_COLS) | {"_regime", "_session"}
    return [c for c in X.columns
            if (c.startswith("f_") or c.startswith("mf_") or c.startswith("psy_"))
            and c not in skip]


def uniqueness_weights(y: pd.Series, horizon: int = HORIZON) -> pd.Series:
    """Lopez de Prado average uniqueness: 1 / mean concurrency over the span."""
    n = len(y)
    labelled = np.where(y.notna().values)[0]
    conc = np.zeros(n, dtype=float)
    for i in labelled:
        conc[i + 1: i + 1 + horizon] += 1.0
    w = np.full(n, np.nan)
    for i in labelled:
        span = slice(i + 1, min(i + 1 + horizon, n))
        c = conc[span]
        w[i] = (len(c) / c.sum()) if c.sum() > 0 else 1.0
    return pd.Series(w, index=y.index)


def sample_weights(X: pd.DataFrame, ycol: str = "label_hpe_tp_before_sl") -> pd.Series:
    w = uniqueness_weights(X[ycol])
    for col, prefix in (("_regime", "regime"), ("_session", "session")):
        if col in X.columns:
            freq = X.groupby(col)[col].transform("count") / len(X)
            w = w * (1.0 / freq.clip(lower=0.01))
    if w.notna().any():
        w = w / w.mean()
    return w


# ------------------------------------------------------------------ splits


def purged_walkforward(X: pd.DataFrame, folds: int = FOLDS,
                       embargo: int = EMBARGO,
                       horizon: int = HORIZON
                       ) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds with purge + embargo. Returns (train, test) idx."""
    n = len(X)
    bounds = [int(n * k / folds) for k in range(folds + 1)]
    y = X["label_hpe_tp_before_sl"]
    event_end = pd.Series(np.arange(n, dtype=float), index=X.index)
    lab_pos = np.where(y.notna().values)[0]
    event_end.iloc[lab_pos] = lab_pos + horizon
    out = []
    for k in range(1, folds):
        tr_hi, te_lo, te_hi = bounds[k], bounds[k], bounds[k + 1]
        train = np.arange(0, tr_hi)
        # purge: drop train samples whose event window reaches into
        # [test_start - embargo, test_end] (embargo protects the gap)
        ee = event_end.values[:tr_hi]
        keep = ee < (te_lo - embargo)
        train = train[keep]
        test = np.arange(te_lo, te_hi)
        out.append((train, test))
    return out


# ------------------------------------------------------------------ model


def make_model(seed: int = 666) -> tuple[str, Any]:
    try:
        import xgboost as xgb
        return ("xgboost", xgb.XGBClassifier(
            n_estimators=400, max_depth=3, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.6, reg_lambda=4.0,
            min_child_weight=20, eval_metric="auc", random_state=seed,
            tree_method="hist", n_jobs=4))
    except ImportError:
        pass
    try:
        import lightgbm as lgb
        return ("lightgbm", lgb.LGBMClassifier(
            n_estimators=400, num_leaves=24, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.6, reg_lambda=4.0,
            min_child_samples=20, random_state=seed, verbosity=-1, n_jobs=4))
    except ImportError:
        pass
    from sklearn.ensemble import HistGradientBoostingClassifier
    return ("sklearn", HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=300, random_state=seed))


def make_calibrator(n_rows: int):
    try:
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        # paper-minimal installs ship no sklearn: pass raw probabilities
        # through, honestly labeled - never pretend calibration happened
        class Passthrough:
            kind = "passthrough (sklearn unavailable)"

            def fit(self, p: np.ndarray, y: np.ndarray) -> "Passthrough":
                return self

            def predict(self, p: np.ndarray) -> np.ndarray:
                return np.clip(p, 0.0, 1.0)

        return Passthrough()

    class Cal:
        def __init__(self) -> None:
            self.kind = "isotonic" if n_rows >= 1000 else "platt"
            self.iso: IsotonicRegression | None = None
            self.lr: LogisticRegression | None = None

        def fit(self, p: np.ndarray, y: np.ndarray) -> "Cal":
            if self.kind == "isotonic":
                self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                self.iso.fit(p, y)
            else:
                self.lr = LogisticRegression()
                self.lr.fit(p.reshape(-1, 1), y)
            return self

        def predict(self, p: np.ndarray) -> np.ndarray:
            if self.iso is not None:
                return self.iso.predict(p)
            if self.lr is None:
                raise RuntimeError("calibrator not fitted")
            return self.lr.predict_proba(p.reshape(-1, 1))[:, 1]

    return Cal()


def manual_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based AUC without sklearn (ties get half credit)."""
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    n_pos = float((y == 1).sum())
    n_neg = float((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0)
                 / (n_pos * n_neg))


def fold_pf(r_multiples: np.ndarray) -> float | None:
    pos = r_multiples[r_multiples > 0].sum()
    neg = abs(r_multiples[r_multiples <= 0].sum())
    return float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else 0.0)


def walkforward(X: pd.DataFrame, folds: list[tuple[np.ndarray, np.ndarray]]
                ) -> tuple[pd.Series, list[dict], list, dict]:
    """Expanding-window training with inner calibration. Returns OOS probs
    plus the LAST fold's fitted (model, calibrator) so the production
    artifact can carry a servable model instead of an empty shell."""
    cols = feature_columns(X)
    Xf = X[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
    y = X["label_hpe_tp_before_sl"].astype(float)
    r_mult = X["label_hpe_r_multiple"]
    w_all = sample_weights(X)
    probs = pd.Series(np.nan, index=X.index)
    reports: list[dict] = []
    engines: list[str] = []
    artifacts: dict = {"model": None, "calibrator": None, "columns": cols}
    try:
        from sklearn.metrics import roc_auc_score
    except ImportError:
        roc_auc_score = None

    for k, (tr, te) in enumerate(folds):
        tr_lab = tr[np.isfinite(y.values[tr])]
        if len(tr_lab) < 800:
            reports.append({"fold": k, "skipped": "insufficient labelled rows",
                            "train_rows": int(len(tr_lab))})
            continue
        # inner calibration slice: last 20% of the training span
        cut = int(len(tr_lab) * 0.8)
        fit_idx, cal_idx = tr_lab[:cut], tr_lab[cut:]
        try:
            engine, model = make_model()
        except ImportError:
            reports.append({"fold": k, "skipped":
                            "no ML engine available (install scikit-learn "
                            "or xgboost) - ml abstains honestly",
                            "train_rows": int(len(tr_lab))})
            continue
        engines.append(engine)
        model.fit(Xf.values[fit_idx], y.values[fit_idx].astype(int),
                  sample_weight=w_all.values[fit_idx])
        p_cal = model.predict_proba(Xf.values[cal_idx])[:, 1]
        cal = make_calibrator(len(cal_idx)).fit(p_cal, y.values[cal_idx])
        p_te = cal.predict(model.predict_proba(Xf.values[te])[:, 1])
        probs.iloc[te] = np.clip(p_te, 0.0, 1.0)
        artifacts = {"model": model, "calibrator": cal, "columns": cols}

        te_lab_mask = np.isfinite(y.values[te])
        y_te, p_te_lab = y.values[te][te_lab_mask], p_te[te_lab_mask]
        if roc_auc_score is not None:
            auc = float(roc_auc_score(y_te, p_te_lab)) if len(y_te) > 10 \
                and len(set(y_te)) > 1 else float("nan")
        else:
            auc = manual_auc(y_te, p_te_lab) if len(y_te) > 10 else float("nan")
        taken = p_te_lab >= PROB_THRESHOLD
        pf = fold_pf(r_mult.values[te][te_lab_mask][taken]) \
            if taken.sum() >= MIN_TAKEN else None
        base = float(y_te.mean()) if len(y_te) else float("nan")
        reports.append({
            "fold": k, "engine": engine, "train_rows": int(len(fit_idx)),
            "cal_rows": int(len(cal_idx)), "test_rows": int(len(te)),
            "labelled_test": int(te_lab_mask.sum()), "base_rate": round(base, 4),
            "auc": round(auc, 4), "taken": int(taken.sum()),
            "pf_taken": None if pf is None else (round(pf, 3)
                                                 if np.isfinite(pf) else 999.0),
        })
    return probs, reports, engines, artifacts


class SequenceModel:
    """Temporal CNN + attention over 32-bar feature windows (torch, opt-in).

    SOFT VOTE ONLY - even a passing sequence model never becomes a hard gate.
    Requires torch; without it this class reports unavailable honestly.
    """

    LEN = 32

    def __init__(self) -> None:
        self.available = False
        self.model: Any = None
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None
        try:
            import torch  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False

    def _net(self, n_feat: int):
        import torch
        import torch.nn as nn

        class Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.conv = nn.Sequential(
                    nn.Conv1d(n_feat, 64, 5, padding=2), nn.ReLU(),
                    nn.Conv1d(64, 64, 5, padding=2), nn.ReLU(),
                    nn.Conv1d(64, 32, 3, padding=1), nn.ReLU())
                self.attn = nn.Linear(32, 1)
                self.head = nn.Sequential(nn.Linear(32, 16), nn.ReLU(),
                                          nn.Linear(16, 1))

            def forward(self, x):                      # x: (B, L, F)
                h = self.conv(x.permute(0, 2, 1)).permute(0, 2, 1)
                a = torch.softmax(self.attn(h), dim=1)
                z = (a * h).sum(dim=1)
                return self.head(z).squeeze(-1)

        return Net()

    def fit(self, Xf: np.ndarray, y: np.ndarray, idx: np.ndarray,
            epochs: int = 6, seed: int = 666) -> bool:
        if not self.available or len(idx) < 2000:
            return False
        import torch
        torch.manual_seed(seed)
        vals = Xf[idx]
        self.mean_ = vals.mean(axis=0)
        self.std_ = np.where(vals.std(axis=0) == 0, 1.0, vals.std(axis=0))
        rows = idx[idx >= self.LEN]
        seqs = (Xf[[np.arange(r - self.LEN + 1, r + 1) for r in rows[:4000]]]
                - self.mean_) / self.std_
        targets = y[rows[:4000]]
        Xt = torch.tensor(seqs, dtype=torch.float32).transpose(1, 2)
        yt = torch.tensor(targets, dtype=torch.float32)
        self.model = self._net(Xt.shape[1])
        opt = torch.optim.Adam(self.model.parameters(), lr=1e-3)
        lossf = torch.nn.BCEWithLogitsLoss()
        self.model.train()
        for _ in range(epochs):
            perm = torch.randperm(len(Xt))
            for i in range(0, len(Xt), 256):
                b = perm[i:i + 256]
                opt.zero_grad()
                loss = lossf(self.model(Xt[b]), yt[b])
                loss.backward()
                opt.step()
        return True

    def predict(self, Xf: np.ndarray, idx: np.ndarray) -> np.ndarray | None:
        if not self.available or self.model is None:
            return None
        import torch
        rows = idx[idx >= self.LEN]
        seqs = (Xf[[np.arange(r - self.LEN + 1, r + 1) for r in rows]]
                - self.mean_) / self.std_
        Xt = torch.tensor(seqs, dtype=torch.float32).transpose(1, 2)
        self.model.eval()
        with torch.no_grad():
            out = self.model(Xt).numpy()
        p = 1.0 / (1.0 + np.exp(-out))
        full = np.full(len(idx), np.nan)
        full[np.searchsorted(idx, rows)] = p
        return full


# ------------------------------------------------------------------ gate


def evaluate_gate(reports: list[dict]) -> tuple[bool, str]:
    """Pooled OOS AUC >= 0.58 AND worst-fold PF >= 1.2 (folds with < MIN_TAKEN
    taken signals count as failing - tiny samples prove nothing)."""
    oos = [r for r in reports if not r.get("skipped")]
    if not oos:
        return False, "no OOS folds evaluated"
    aucs = [r["auc"] for r in oos if np.isfinite(r.get("auc", np.nan))]
    if not aucs:
        return False, "no fold produced a measurable AUC"
    pooled_auc = float(np.mean(aucs))
    pfs = [r["pf_taken"] for r in oos]
    if any(p is None for p in pfs):
        return False, "a fold took < MIN_TAKEN signals at the threshold"
    worst_pf = float(min(pfs))
    ok_auc = pooled_auc >= GATE_AUC
    ok_pf = worst_pf >= GATE_WORST_PF
    why = f"pooled AUC {pooled_auc:.3f} (>= {GATE_AUC}) | worst-fold PF {worst_pf:.2f} " \
          f"(>= {GATE_WORST_PF})"
    return (ok_auc and ok_pf), why


# ------------------------------------------------------------------ main


def main() -> int:
    print("=" * 72)
    print(" GOLD REAPER HPE :: meta-model training (Phase 4)")
    print("=" * 72)
    store = FeatureStore()
    X = assemble_dataset(store)
    store.close()
    cols = feature_columns(X)
    print(f" dataset : {len(X):,} rows | {len(cols)} features "
          f"(f_*/mf_*/psy_*) | base rate "
          f"{X['label_hpe_tp_before_sl'].mean() * 100:.1f}% TP-first")

    folds = purged_walkforward(X)
    print(f" CV      : expanding-window, {len(folds)} OOS folds, "
          f"purge+embargo {EMBARGO} bars")
    probs, reports, engines, artifacts = walkforward(X, folds)

    seq = SequenceModel()
    seq_note = "torch sequence model: UNAVAILABLE (soft vote = boosted trees)"
    if seq.available and os.environ.get("HPE_SEQ", "0") == "1":
        Xf = X[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0).values
        y = X["label_hpe_tp_before_sl"].values.astype(float)
        for k, (tr, te) in enumerate(folds):
            p = None
            if seq.fit(Xf, y, tr):
                p = seq.predict(Xf, te)
            if p is not None and not np.isnan(p).all():
                blend = probs.iloc[te].values
                probs.iloc[te] = np.where(np.isnan(blend), p,
                                          0.7 * blend + 0.3 * p)
        seq_note = "torch sequence model: blended at 0.3 weight (soft vote only)"
    print(f" seq     : {seq_note}")

    print("-" * 72)
    print(f" {'fold':<5} {'engine':<10} {'train':>7} {'AUC':>7} {'taken':>6} "
          f"{'PF':>8} {'base':>7}")
    for r in reports:
        if r.get("skipped"):
            print(f" {r['fold']:<5} {'-':<10} {r['train_rows']:>7}  skipped: "
                  f"{r['skipped']}")
            continue
        pf = r["pf_taken"]
        pf_txt = "inf" if pf == 999.0 else ("none" if pf is None
                                            else format(pf, "8.3f"))
        print(f" {r['fold']:<5} {r['engine']:<10} {r['train_rows']:>7,} "
              f"{r['auc']:>7.4f} {r['taken']:>6} {pf_txt:>8} "
              f"{r['base_rate'] * 100:>6.1f}%")
    print("-" * 72)

    gate, why = evaluate_gate(reports)
    verdict = "PROMOTED - armed as hard gate" if gate else \
        "REJECTED - stays a SOFT vote (never forced)"
    print(f" gate    : {verdict}")
    print(f"          {why}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    import joblib
    last = [r for r in reports if not r.get("skipped")]
    engine = last[-1]["engine"] if last else "none"
    # the artifact now carries the fitted model + calibrator so live
    # explain/prob paths actually serve THIS label geometry (the old
    # dump was an empty shell: no model, no calibration, ever)
    joblib.dump({"engine": engine, "columns": cols, "armed": gate,
                 "model": artifacts.get("model"),
                 "calibrator": artifacts.get("calibrator"),
                 "probs_head": float(probs.dropna().iloc[0])
                 if probs.notna().any() else 0.5}, ART_MODEL)
    ART_META.write_text(json.dumps({
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(X)), "features": len(cols), "engine": engine,
        "armed": gate, "gate_report": why, "folds": reports,
        "sequence_model": seq_note,
        "geometry": {"tp_r": HPE_TP_R, "sl_atr": HPE_SL_ATR, "horizon": HORIZON},
        "rules": {"auc_gate": GATE_AUC, "worst_pf_gate": GATE_WORST_PF,
                  "prob_threshold": PROB_THRESHOLD, "min_taken": MIN_TAKEN},
    }, indent=2, default=str))
    print(f" artifact: {ART_MODEL.name} + {ART_META.name} (armed={gate})")
    print("=" * 72)
    return 0 if gate else 2


if __name__ == "__main__":
    sys.exit(main())
