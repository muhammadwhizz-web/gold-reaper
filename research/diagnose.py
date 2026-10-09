"""
GOLD REAPER :: Signal Diagnosis (the microscope)
=================================================
The README published losing walk-forward numbers. This script answers WHY,
bucket by bucket, and tests every cheap hypothesis the honest playbook
allows. Findings feed docs/DIAGNOSIS.md and research/tune_v3.py.

Questions answered (all on the canonical walk-forward window):
  1. Which regimes lose money?        (bucket by entry regime)
  2. Which hours lose money?          (bucket by entry UTC hour)
  3. Which weekdays lose money?       (bucket by entry weekday)
  4. Which modules lose money?        (trend / meanrev / breakout traces)
  5. Does SL-first over-penalize?     (re-test with TP-first)
  6. Are cost assumptions realistic?  (spread/slippage grid)
  7. Where does volatility sit?       (entry ATR-percentile buckets)
  8. Does ML improve on 20y daily?    (expanding-window daily AUC probe)
  9. Does the hunt work elsewhere?    (silver, EURUSD - cross-asset check)

Usage:
  python research/diagnose.py           # full (needs yfinance for #9)
  python research/diagnose.py --quick   # offline buckets only (CI-safe)
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.indicators import atr, ema, rsi  # noqa: E402
from features.store import FeatureStore  # noqa: E402
from research.backtest_apex import (  # noqa: E402
    FOLDS,
    fold_nets,
    load_all,
    simulate,
    stats_from,
)

OUT_JSON = ROOT / "data" / "diagnosis.json"


def _bucket(trades: list[dict], key_fn) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return pd.DataFrame()
    df["k"] = df["entry_ts"].map(key_fn)
    g = df.groupby("k")["pnl"]
    out = pd.DataFrame({
        "n": g.count(),
        "net": g.sum().round(2),
        "wr%": (df.assign(w=df["pnl"] > 0).groupby("k")["w"].mean() * 100).round(1),
        "avg": g.mean().round(2),
    })
    return out.sort_values("net")


def _show(title: str, table: pd.DataFrame) -> None:
    print(f"\n--- {title} ---")
    if table.empty:
        print("  (no trades)")
        return
    print(table.to_string())


def hourly_vol_rank(h1: pd.DataFrame) -> pd.Series:
    a = atr(h1, 14) / h1["close"]
    return a.rolling(500, min_periods=100).rank(pct=True)


def daily_ml_probe(d1: pd.DataFrame) -> dict:
    """20y daily expanding-window AUC probe (longer-window ML question)."""
    try:
        import xgboost as xgb
    except ImportError:
        return {"error": "xgboost missing"}
    c = d1["close"]
    f = pd.DataFrame({
        "ret1": c.pct_change(),
        "ret5": c.pct_change(5),
        "ret20": c.pct_change(20),
        "rsi": rsi(c, 14),
        "atrp": atr(d1, 14) / c,
        "emadist": (c - ema(c, 50)) / c,
        "volr": (c.pct_change().rolling(20).std()
                 / c.pct_change().rolling(120).std()),
    }).replace([np.inf, -np.inf], np.nan)
    f["volr"] = f["volr"].clip(-5, 5)
    atr14 = atr(d1, 14)
    fwd = c.shift(-5) - c
    dead = 1.0 * atr14
    y = pd.Series(np.nan, index=d1.index)
    y[fwd > dead] = 1
    y[fwd < -dead] = 0
    f = f.ffill().fillna(0.0)
    n = len(d1)
    folds = 5
    aucs = []
    for k in range(1, folds):
        lo_tr, hi_tr = 0, int(n * k / folds)
        lo_te, hi_te = int(n * k / folds), int(n * min((k + 1) / folds, 1.0))
        ytr = y.iloc[lo_tr:hi_tr].dropna()
        if len(ytr) < 200:
            continue
        m = xgb.XGBClassifier(n_estimators=250, max_depth=3, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.7,
                              reg_lambda=4.0, min_child_weight=15,
                              eval_metric="auc", random_state=666,
                              tree_method="hist", n_jobs=4)
        m.fit(f.iloc[ytr.index].values if False else f.loc[ytr.index].values,
              ytr.values.astype(int), verbose=False)
        from sklearn.metrics import roc_auc_score
        te = y.iloc[lo_te:hi_te].dropna()
        if len(te) < 30 or te.nunique() < 2:
            aucs.append(None)
            continue
        p = m.predict_proba(f.loc[te.index].values)[:, 1]
        aucs.append(round(float(roc_auc_score(te.values.astype(int), p)), 3))
    aucs = [a for a in aucs if a is not None]
    return {"fold_aucs": aucs, "mean_auc": round(float(np.mean(aucs)), 3) if aucs else None,
            "gate": "PASS (>=0.56)" if aucs and np.mean(aucs) >= 0.56 else "FAIL (<0.56)"}


def cross_asset_check(quick: bool) -> dict:
    """Run the same hunt on silver and EURUSD (trend module + ensemble)."""
    if quick:
        return {"skipped": "quick mode (no network)"}
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance missing"}
    out = {}
    for name, ticker in (("XAGUSD(silver)", "SI=F"), ("EURUSD", "EURUSD=X")):
        try:
            raw = yf.download(ticker, period="730d", interval="1h",
                              auto_adjust=True, progress=False)
            if raw is None or raw.empty:
                out[name] = {"error": "no data returned"}
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [str(c).lower() for c in raw.columns]
            h1 = raw[["open", "high", "low", "close"]].dropna()
            h1.index = pd.to_datetime(h1.index, utc=True)
            h1 = h1[~h1.index.duplicated(keep="last")].sort_index()
            if len(h1) < 800:
                out[name] = {"error": f"only {len(h1)} bars"}
                continue
            h4 = (h1.resample("4h")
                  .agg({"open": "first", "high": "max", "low": "min",
                        "close": "last"}).dropna())
            t_trend = simulate(h1, h4, None, mode="baseline",
                               cfg={"modules": ("trend",)})
            t_ens = simulate(h1, h4, None, mode="baseline")
            out[name] = {
                "bars": len(h1), "range": f"{h1.index[0].date()}..{h1.index[-1].date()}",
                "trend_only": stats_from(t_trend),
                "ensemble": stats_from(t_ens),
            }
        except Exception as exc:  # noqa: BLE001
            out[name] = {"error": str(exc)[:120]}
    return out


def main() -> int:
    quick = "--quick" in sys.argv
    store = FeatureStore()
    h1, h4, feats = load_all(store)
    d1 = store.read_bars("1d")
    store.close()
    if h1.empty:
        print("[!] run ingestion first")
        return 1
    n = len(h1)
    bounds = [h1.index[min(int(n * k / FOLDS), n - 1)] for k in range(FOLDS + 1)]
    folds = [(bounds[k], bounds[k + 1]) for k in range(FOLDS)]
    print(f"[diagnose] window {h1.index[0].date()} .. {h1.index[-1].date()} "
          f"({len(h1):,} bars, {FOLDS} folds)")
    report: dict = {"window": [str(h1.index[0]), str(h1.index[-1])]}

    # ---- canonical ledger: ensemble without ML (what the README called REAPER-X)
    ledger = simulate(h1, h4, feats, mode="baseline")
    st = stats_from(ledger)
    print(f"\n[baseline ensemble] {st['trades']} trades, net {st['net']:+,.2f}, "
          f"PF {st['pf']:.2f}, DD {st['dd']:.1f}%")
    report["baseline"] = st

    # ---- 1-4: buckets -------------------------------------------------
    vol_r = hourly_vol_rank(h1)
    buckets = {
        "by_regime": _bucket(ledger, lambda ts: _regime_at(h1, feats, ts)),
        "by_entry_hour": _bucket(ledger, lambda ts: ts.hour),
        "by_weekday": _bucket(ledger, lambda ts: ts.strftime("%a")),
        "by_module": _bucket(ledger, lambda ts: _trace_at(ledger, ts)),
        "by_vol_rank": _bucket(ledger, lambda ts: _vol_bucket(vol_r, ts)),
    }
    for name, tbl in buckets.items():
        _show(name, tbl)
        report[name] = json.loads(tbl.to_json(orient="index"))

    # ---- 5: SL-first vs TP-first --------------------------------------
    tp_first = simulate(h1, h4, feats, mode="baseline", cfg={"sl_first": False})
    stp = stats_from(tp_first)
    print(f"\n[SL-first vs TP-first] SL-first net {st['net']:+,.2f} "
          f"({st['trades']} trades) | TP-first net {stp['net']:+,.2f} "
          f"({stp['trades']} trades) | delta {stp['net'] - st['net']:+,.2f}")
    report["sl_first_vs_tp_first"] = {"sl_first": st, "tp_first": stp}

    # ---- 6: cost grid ---------------------------------------------------
    grid = {}
    for spread in (0.20, 0.35, 0.50, 0.70):
        row = {}
        for slip in (0.05, 0.15):
            t = simulate(h1, h4, feats, mode="baseline",
                         cfg={"spread": spread, "slippage": slip})
            s2 = stats_from(t)
            row[f"slip={slip}"] = f"{s2['net']:+,.0f} ({s2['trades']}t)"
        grid[f"spread={spread}"] = row
    print("\n--- cost sensitivity (net USD) ---")
    for k, v in grid.items():
        print(f"  {k:<12} {v}")
    report["cost_grid"] = grid

    # ---- folds table ----------------------------------------------------
    fn = fold_nets(ledger, folds)
    print("\n--- fold nets (ensemble, no ML) ---")
    for (lo, hi), v in zip(folds, fn):
        print(f"  fold {h1.index.get_loc(lo) // max(1, n // FOLDS)}: "
              f"{str(lo.date())} .. {str(hi.date())}  {v:+,.2f}")
    report["fold_nets"] = fn

    # ---- 8: daily ML probe ----------------------------------------------
    if not d1.empty:
        print("\n--- daily 20y ML probe (expanding 5-fold AUC) ---")
        probe = daily_ml_probe(d1)
        print(f"  {probe}")
        report["daily_ml_probe"] = probe

    # ---- 9: cross-asset ---------------------------------------------------
    print("\n--- cross-asset (same hunt, other symbols) ---")
    xa = cross_asset_check(quick)
    for k, v in xa.items():
        if not isinstance(v, dict) or "error" in v or "skipped" in v:
            print(f"  {k}: {v}")
        else:
            print(f"  {k} [{v['range']}] trend net {v['trend_only']['net']:+,.0f} "
                  f"({v['trend_only']['trades']}t) | ensemble net "
                  f"{v['ensemble']['net']:+,.0f} ({v['ensemble']['trades']}t)")
    report["cross_asset"] = xa

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n[diagnose] full report -> {OUT_JSON.relative_to(ROOT)}")
    return 0


def _regime_at(h1: pd.DataFrame, feats: pd.DataFrame, ts) -> str:
    return str(_cache_regime(h1).asof(ts))


def _trace_at(ledger: list[dict], ts) -> str:
    for t in ledger:
        if t["entry_ts"] == ts:
            return t.get("trace", "?")
    return "?"


_VOL_BUCKETS = ((0.0, 0.2, "V<20"), (0.2, 0.4, "V20-40"), (0.4, 0.6, "V40-60"),
                (0.6, 0.8, "V60-80"), (0.8, 1.01, "V>80"))


def _vol_bucket(vol_r: pd.Series, ts) -> str:
    v = vol_r.asof(ts)
    if pd.isna(v):
        return "NA"
    for lo, hi, name in _VOL_BUCKETS:
        if lo <= v < hi:
            return name
    return "NA"


_REG_CACHE: dict[int, pd.Series] = {}


def _cache_regime(h1: pd.DataFrame) -> pd.Series:
    key = id(h1)
    if key not in _REG_CACHE:
        from core.indicators import adx
        ret = h1["close"].pct_change()
        a = atr(h1, 14) / h1["close"]
        vp = a.rolling(500, min_periods=100).rank(pct=True)
        vr = ret.rolling(20).std() / ret.rolling(120).std().replace(0, np.nan)
        ed = (h1["close"] - ema(h1["close"], 50)) / h1["close"]
        dx = adx(h1, 14)
        reg = pd.Series("RANGE", index=h1.index)
        reg[(vr > 2.2) & (vp > 0.97)] = "CRISIS"
        reg[(vr > 1.6) & (vp <= 0.97)] = "VOLATILE_CHOP"
        reg[(dx > 25) & (ed > 0.004) & (vr <= 1.6)] = "TREND_UP"
        reg[(dx > 25) & (ed < -0.004) & (vr <= 1.6)] = "TREND_DOWN"
        _REG_CACHE[key] = reg
    return _REG_CACHE[key]


if __name__ == "__main__":
    sys.exit(main())
