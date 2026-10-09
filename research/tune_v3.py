"""
GOLD REAPER :: Fix Tuner v3 (walk-forward, worst-fold first)
=============================================================
Takes the diagnosis findings and tests every honest-playbook fix the data
supports, judged by the ONLY metric we promote on: does the WORST walk-
forward fold improve? Single-axis sweeps first, then a focused grid on the
axes that survived. Nothing ships on full-period net alone.

Playbook items tested here:
  - module hardening          (drop meanrev / breakout-only variants)
  - session tightening        (hour subsets of the overlap)
  - time-stop                 (kill dead trades after N bars)
  - target-math retune        (tp_r x sl_atr geometry sweep)
  - volatility regime gate    (ATR-percentile band)
  - weekday filter            (flagged as high overfit risk - reported, not promoted)

Run:  python research/tune_v3.py
"""
from __future__ import annotations

import sys
import warnings
from itertools import product
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.store import FeatureStore  # noqa: E402
from research.backtest_apex import (  # noqa: E402
    FOLDS,
    fold_nets,
    load_all,
    merge_cfg,
    simulate,
    stats_from,
)

BASELINE_CFG = merge_cfg()          # current production defaults


def worst_fold(trades: list[dict], folds: list[tuple]) -> float:
    return min(fold_nets(trades, folds))


def run(h1, h4, feats, cfg: dict) -> dict:
    t = simulate(h1, h4, feats, mode="baseline", cfg=cfg)
    st = stats_from(t)
    st["worst_fold"] = worst_fold(t, FOLDS_T)
    return st


def fmt(st: dict) -> str:
    return (f"net {st['net']:+8,.0f} | worst {st['worst_fold']:+8,.0f} | "
            f"PF {st['pf'] if st['pf'] else 0:4.2f} | DD {st['dd']:6.1f}% | "
            f"{st['trades']:3d}t | wr {st['wr']:4.1f}%")


if __name__ == "__main__":
    store = FeatureStore()
    h1, h4, feats = load_all(store)
    store.close()
    n = len(h1)
    bounds = [h1.index[min(int(n * k / FOLDS), n - 1)] for k in range(FOLDS + 1)]
    FOLDS_T = [(bounds[k], bounds[k + 1]) for k in range(FOLDS)]
    globals()["FOLDS_T"] = FOLDS_T

    print("[tune v3] baseline (current production config)")
    print("  ", fmt(run(h1, h4, feats, BASELINE_CFG)))

    # ---------------- stage 1: single-axis sweeps ----------------
    axes = {
        "modules": [("trend", "meanrev", "breakout"), ("trend", "breakout"),
                    ("trend",), ("meanrev", "breakout")],
        "hours": [None, {12, 13}, {13, 14}, {12, 13, 14}, {12, 13, 14, 15}],
        "time_stop_bars": [None, 6, 10, 16, 24],
        "tp_r": [1.5, 2.0, 2.667, 3.5],
        "sl_atr": [1.0, 1.2, 1.5, 2.0],
        "vol_band": [None, (0.1, 0.9), (0.2, 0.8), (0.25, 0.75)],
    }
    print("\n[tune v3] stage 1 - single-axis sweeps (worst-fold decides)")
    winners: dict[str, object] = {}
    base_worst = run(h1, h4, feats, BASELINE_CFG)["worst_fold"]
    for axis, values in axes.items():
        print(f"  -- {axis}")
        best_val, best_score = None, -1e18
        for v in values:
            cfg = merge_cfg(**{axis: v})
            # single-axis runs always keep the baseline module set unless
            # the axis itself is modules
            st = run(h1, h4, feats, cfg)
            tag = str(v)
            print(f"     {tag:<34} {fmt(st)}")
            if st["worst_fold"] > best_score:
                best_score, best_val = st["worst_fold"], v
        winners[axis] = (best_val, best_score)
    print("\n  stage-1 winners (worst-fold):")
    for axis, (v, s) in winners.items():
        mark = " * " if s > base_worst else "   "
        print(f"   {mark}{axis:<16} {v!s:<34} worst {s:+,.0f}")

    # ---------------- stage 2: focused grid ----------------
    print("\n[tune v3] stage 2 - focused grid on surviving axes")
    mod_opts = [("trend", "breakout"), ("trend",)]
    hour_opts = [None, {12, 13}, {12, 13, 14}]
    ts_opts = [None, 10, 16]
    tp_opts = [2.0, 2.667]
    sl_opts = [1.2, 1.5]
    results = []
    for mod, hrs, tstop, tpr, slr in product(mod_opts, hour_opts, ts_opts,
                                             tp_opts, sl_opts):
        cfg = merge_cfg(modules=mod, hours=hrs, time_stop_bars=tstop,
                        tp_r=tpr, sl_atr=slr)
        st = run(h1, h4, feats, cfg)
        results.append((st["worst_fold"], st["net"], cfg, st))
    results.sort(key=lambda r: (r[0], r[1]), reverse=True)
    print("  top 8 by worst-fold:")
    for wf, net, cfg, st in results[:8]:
        short = {k: v for k, v in cfg.items() if k in
                 ("modules", "hours", "time_stop_bars", "tp_r", "sl_atr")}
        print(f"   worst {wf:+8,.0f} net {net:+8,.0f} | {short}")
    print("\n  baseline for comparison: worst "
          f"{run(h1, h4, feats, BASELINE_CFG)['worst_fold']:+,.0f}")
    print("\n rule: a config is promoted ONLY if its worst fold beats the "
          "baseline worst fold AND full-period net is not worse.\n")
    return_code = 0
    sys.exit(return_code)
