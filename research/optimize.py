"""
GOLD REAPER :: Walk-Forward Optimizer
=====================================
Sweeps the REAPER-X parameter space across 4 chronological folds and
ranks by the WORST fold result (robustness over glory).

A config that only wins in one regime is a trap. We want plateaus.

Usage: python research/optimize.py
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import CONFIG  # noqa: E402
from core.logger import GREEN, RED, YELLOW, cprint  # noqa: E402
from research.backtest import load, run_backtest  # noqa: E402

FOLDS = 4


def main() -> int:
    h1 = load(ROOT / "data" / "history" / "XAUUSD_H1.csv")
    h4 = load(ROOT / "data" / "history" / "XAUUSD_H4.csv")

    # chronological fold boundaries
    n = len(h1)
    bounds = [h1.index[min(int(n * i / FOLDS), n - 1)] for i in range(FOLDS + 1)]
    folds = []
    for k in range(FOLDS):
        h1f = h1[(h1.index >= bounds[k]) & (h1.index < bounds[k + 1])]
        h4f = h4[(h4.index >= bounds[k]) & (h4.index < bounds[k + 1])]
        folds.append((h1f, h4f))
        cprint(f"[*] fold {k + 1}: {len(h1f):,} h1 bars "
               f"({h1f.index[0].date()} .. {h1f.index[-1].date()})", YELLOW)

    # overlap-only hunting (recon: the kill zone is 12-16 UTC)
    base = replace(CONFIG, trade_london=False, trade_overlap=True, trade_asia=False,
                   trade_newyork=False, min_atr_price=3.0)

    grid = list(itertools.product(
        [22, 24, 26],         # min_adx
        [1.2, 1.4, 1.6],      # sl_atr_mult
        [2.4, 2.8, 3.2],      # tp_atr_mult
        [1.0, 1.5],           # breakeven_at_r
    ))
    cprint(f"[*] sweeping {len(grid)} configs x {FOLDS} folds...", YELLOW)

    rows = []
    for adx_min, sl_m, tp_m, be_r in grid:
        cfg = replace(base, min_adx=adx_min, sl_atr_mult=sl_m, tp_atr_mult=tp_m,
                      breakeven_at_r=be_r)
        fold_pnls = []
        total_trades = 0
        for h1f, h4f in folds:
            r = run_backtest(h1f, h4f, verbose=False, cfg=cfg)
            fold_pnls.append(r["net_pnl"])
            total_trades += r["trades"]
        rows.append({
            "adx": adx_min, "sl": sl_m, "tp": tp_m, "be": be_r,
            "trades": total_trades,
            "worst_fold": min(fold_pnls),
            "mean_fold": sum(fold_pnls) / len(fold_pnls),
            "positive_folds": sum(1 for p in fold_pnls if p > 0),
            "folds": [round(p) for p in fold_pnls],
        })

    rdf = pd.DataFrame(rows)
    rdf = rdf[rdf["trades"] >= 20]
    rdf = rdf.sort_values(["worst_fold", "mean_fold"], ascending=False)

    cprint("\nTOP 10 WALK-FORWARD ROBUST CONFIGS (ranked by worst fold):", YELLOW)
    print(rdf.head(10).to_string(index=False,
          float_format=lambda x: f"{x:.2f}"))

    if len(rdf) == 0:
        cprint("[!] no config survived the minimum-trade filter", RED)
        return 1

    best = rdf.iloc[0]
    cprint(f"\n[★] CHOSEN: adx>={best['adx']:.0f} sl={best['sl']:.1f}xATR "
           f"tp={best['tp']:.1f}xATR be={best['be']:.1f}R | worst fold "
           f"{best['worst_fold']:+,.0f} | {best['positive_folds']}/4 folds green",
           GREEN)

    # final full-period verdict with chosen config
    final_cfg = replace(base, min_adx=float(best["adx"]), sl_atr_mult=float(best["sl"]),
                        tp_atr_mult=float(best["tp"]), breakeven_at_r=float(best["be"]))
    cprint("\n[*] full-period verdict with chosen config:", YELLOW)
    run_backtest(h1, h4, verbose=True, cfg=final_cfg)

    cprint("\n[*] write these into .env to lock the config:", YELLOW)
    print(f"MIN_ADX={best['adx']:.0f}\nSL_ATR_MULT={best['sl']:.1f}\n"
          f"TP_ATR_MULT={best['tp']:.1f}\nBREAKEVEN_AT_R={best['be']:.1f}\n"
          f"TRADE_LONDON=false\nTRADE_OVERLAP=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
