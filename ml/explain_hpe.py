"""
GOLD REAPER HPE :: Trade Explainer (Phase 4 - explainability)
=============================================================
You always know why. Two layers:

  1. MODEL reasons  - per-prediction top feature attributions. SHAP
     (TreeExplainer) when the shap package is installed; otherwise an
     honest fallback: global feature importances x the row's z-scores,
     clearly labeled as an approximation.
  2. ENSEMBLE reasons - the decision's vote matrix and gate trail from
     core.ensemble_hpe.Decision, formatted for logs / dashboard / Telegram.

Run from code:
    from ml.explain_hpe import top_model_reasons, format_decision
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def top_model_reasons(artifact: dict, x_row: np.ndarray | pd.Series,
                      k: int = 5) -> tuple[list[tuple[str, float]], str]:
    """Return ([(feature, contribution)], method). contribution is signed for
    SHAP; for the fallback it is importance x |row z| (unsigned, documented)."""
    cols = artifact.get("columns", [])
    model = artifact.get("model")
    row = np.asarray(x_row, dtype=float).reshape(1, -1)
    if model is None or not cols or row.shape[1] != len(cols):
        return [], "unavailable"
    try:                                            # SHAP path
        import shap
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(pd.DataFrame(row, columns=cols))
        vals = sv[-1] if isinstance(sv, list) else (sv[0] if sv.ndim == 3 else sv[0])
        pairs = sorted(zip(cols, np.asarray(vals).ravel()),
                       key=lambda t: -abs(t[1]))[:k]
        return [(c, float(v)) for c, v in pairs], "shap"
    except Exception:                               # noqa: BLE001 - honest fallback
        pass
    try:
        imp = np.asarray(model.feature_importances_, dtype=float)
    except Exception:                               # noqa: BLE001
        return [], "importance-unavailable"
    mu, sd = float(np.nanmean(row)), float(np.nanstd(row))
    z = np.where(sd > 0, (row.ravel() - mu) / sd, 0.0)
    score = imp * np.abs(z)
    pairs = sorted(zip(cols, score), key=lambda t: -t[1])[:k]
    return [(c, float(v)) for c, v in pairs], "importance-z-fallback"


def format_decision(decision_dict: dict) -> list[str]:
    """Human-readable explanation lines from Decision.to_dict()."""
    out = [f"HPE decision: {decision_dict.get('side') or 'NONE'} "
           f"(conf {decision_dict.get('confidence', 0):.2f}, "
           f"{decision_dict.get('aligned', 0)}/{decision_dict.get('required', 4)} "
           f"aligned, {decision_dict.get('regime')}, {decision_dict.get('session')})"]
    for name, v in (decision_dict.get("votes") or {}).items():
        arrow = {1: "LONG", -1: "SHORT", 0: "abstain"}.get(v.get("dir"), "?")
        out.append(f"  {name:<12} {arrow:<8} {v.get('strength', 0):.2f}  "
                   f"{v.get('reason', '')}")
    for v in decision_dict.get("vetoes") or []:
        out.append(f"  VETO {v}")
    for e in decision_dict.get("explain") or []:
        if e.startswith("gate:"):
            out.append(f"  {e}")
    return out


def explain_signal(sig_meta: dict, artifact: dict | None = None,
                   x_row: np.ndarray | None = None) -> list[str]:
    """Full explain mode output for a fired HPE signal."""
    lines = ["=" * 60, " HPE SIGNAL EXPLANATION", "=" * 60]
    lines += format_decision(sig_meta.get("decision", sig_meta))
    if artifact is not None and x_row is not None:
        pairs, method = top_model_reasons(artifact, x_row)
        lines.append(f" model attribution ({method}):")
        for name, val in pairs:
            lines.append(f"   {name:<28} {val:+.4f}")
    lines.append("=" * 60)
    return lines
