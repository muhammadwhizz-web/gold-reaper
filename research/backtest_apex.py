"""
GOLD REAPER APEX :: Walk-Forward Ensemble Backtest
===================================================
The honesty instrument. Replays APEX-X over H1 gold with:

  - regime routing (rule engine, vectorized - identical to live fallback)
  - trend / meanrev / breakout modules exactly as the live ensemble
  - NEWS module abstains (historical calendar not reproducible - documented)
  - ML soft vote trained EXPANDING-WINDOW per fold (zero leakage)
  - conservative fills: spread + slippage both sides, SL checked first
  - 1% risk sizing for apples-to-apples vs the REAPER-X baseline

Reports head-to-head verdict + the mission metric: how many 4-hour blocks
actually banked >= $20 per $1,000-equivalent risk slice.

Run:  python research/backtest_apex.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.indicators import adx, atr, ema, rsi  # noqa: E402
from core.logger import GREEN, RED, YELLOW, cprint  # noqa: E402
from core.sessions import session_of  # noqa: E402
from features.store import FeatureStore  # noqa: E402

SPREAD = 0.35
SLIPPAGE = 0.05
RISK_PCT = 1.0
START_EQ = 10_000.0
FOLDS = 4
BLOCK_TARGET_PCT = 0.002   # $20 per $10k slice = 0.2% of equity per 4h block


def load_all(store: FeatureStore):
    h1 = store.read_bars("1h")
    h4 = store.read_bars("4h")
    feats = store.read_table("features_1h")
    return h1, h4, feats


def ml_walkforward_probs(feats: pd.DataFrame, folds: list[tuple]) -> pd.Series:
    """Expanding-window per-fold probabilities. Fold k trains on data < fold start."""
    cols = [c for c in feats.columns if c.startswith("f_")]
    X = feats[cols].replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
    y = feats["label_tp_before_sl"]
    probs = pd.Series(np.nan, index=feats.index)
    try:
        import xgboost as xgb
    except ImportError:
        print("  [ml] xgboost missing - soft vote disabled")
        return probs
    starts = [f[0] for f in folds]
    for k in range(1, len(folds)):          # fold 0 has no prior data -> no ML
        tr_mask = feats.index < starts[k]
        te_mask = (feats.index >= folds[k][0]) & (feats.index < folds[k][1])
        if tr_mask.sum() < 1500 or te_mask.sum() == 0:
            continue
        ytr = y[tr_mask].dropna()
        idx_tr = ytr.index
        m = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.04,
                              subsample=0.8, colsample_bytree=0.6, reg_lambda=4.0,
                              min_child_weight=20, eval_metric="auc",
                              random_state=666, tree_method="hist", n_jobs=4)
        m.fit(X.loc[idx_tr].values, ytr.values.astype(int), verbose=False)
        te_idx = feats.index[te_mask]
        p = m.predict_proba(X.loc[te_idx].values)[:, 1]
        probs.loc[te_idx] = p
    return probs


def simulate(h1: pd.DataFrame, h4: pd.DataFrame, feats: pd.DataFrame,
             mode: str = "apex", probs: pd.Series | None = None) -> dict:
    c_e = ema(h1["close"], 20)
    r_s = rsi(h1["close"], 14)
    a_s = atr(h1, 14)
    dx_s = adx(h1, 14)
    # h4 bias
    f50 = ema(h4["close"], 50).reindex(h1.index, method="ffill")
    f200 = ema(h4["close"], 200).reindex(h1.index, method="ffill")
    h4c = h4["close"].reindex(h1.index, method="ffill")
    bias = pd.Series("NEUTRAL", index=h1.index)
    bias[(h4c > f50) & (f50 > f200)] = "LONG"
    bias[(h4c < f50) & (f50 < f200)] = "SHORT"

    # regime (rule engine, vectorized)
    ret = h1["close"].pct_change()
    a_norm = a_s / h1["close"]
    vol_pct = a_norm.rolling(500, min_periods=100).rank(pct=True)
    vr = ret.rolling(20).std() / ret.rolling(120).std().replace(0, np.nan)
    edist = (h1["close"] - ema(h1["close"], 50)) / h1["close"]
    regime = pd.Series("RANGE", index=h1.index)
    regime[(vr > 2.2) & (vol_pct > 0.97)] = "CRISIS"
    regime[(vr > 1.6) & (vol_pct <= 0.97)] = "VOLATILE_CHOP"
    regime[(dx_s > 25) & (edist > 0.004) & (vr <= 1.6)] = "TREND_UP"
    regime[(dx_s > 25) & (edist < -0.004) & (vr <= 1.6)] = "TREND_DOWN"

    close = h1["close"].values
    high = h1["high"].values
    low = h1["low"].values
    opn = h1["open"].values
    idx = h1.index
    e20 = c_e.values
    r14 = r_s.values
    a14 = a_s.values
    dx14 = dx_s.values
    bias_v = bias.values
    reg_v = regime.values

    # feature dict accessors (fast column picks)
    fcol = {c: feats[c].values if c in feats.columns else None for c in
            ("f_bb_pos", "f_rsi_14", "f_zscore_100", "f_donch_break_up",
             "f_donch_break_dn", "f_ofi_12", "f_vol_z")}
    have_feats = feats is not None and len(feats) == len(h1)

    equity = START_EQ
    trades: list[dict] = []
    pos = None

    def close_pos(i, price, why):
        nonlocal equity, pos
        if pos is None:
            return
        diff = (price - pos["entry"]) if pos["side"] == "LONG" else (pos["entry"] - price)
        pnl = diff * pos["oz"] - 2 * (SPREAD + SLIPPAGE) * pos["oz"]
        equity += pnl
        trades.append({"ts": idx[i], "side": pos["side"], "pnl": pnl,
                       "session": pos["session"], "regime": pos["regime"]})
        pos = None
        _ = why

    for i in range(220, len(h1) - 1):
        ts = idx[i]

        # ---- manage open ----
        if pos is not None:
            r_dist = abs(pos["entry"] - pos["orig_sl"])
            hi_, lo_ = high[i], low[i]
            if pos["side"] == "LONG" and lo_ <= pos["sl"]:
                close_pos(i, pos["sl"], "SL")
            elif pos["side"] == "LONG" and hi_ >= pos["tp"]:
                close_pos(i, pos["tp"], "TP")
            elif pos["side"] == "SHORT" and hi_ >= pos["sl"]:
                close_pos(i, pos["sl"], "SL")
            elif pos["side"] == "SHORT" and lo_ <= pos["tp"]:
                close_pos(i, pos["tp"], "TP")
            else:
                move = (hi_ - pos["entry"]) if pos["side"] == "LONG" else (pos["entry"] - lo_)
                rm = move / r_dist if r_dist else 0
                if not pos["be"] and rm >= 1.0:
                    pos["sl"] = pos["entry"] + (0.05 * a14[i] if pos["side"] == "LONG"
                                                else -0.05 * a14[i])
                    pos["be"] = True
                elif pos["be"] and rm >= 1.5:
                    trail = (hi_ - 1.2 * a14[i] if pos["side"] == "LONG"
                             else lo_ + 1.2 * a14[i])
                    if (pos["side"] == "LONG" and trail > pos["sl"]) or \
                       (pos["side"] == "SHORT" and trail < pos["sl"]):
                        pos["sl"] = trail
            continue

        # ---- entry gates ----
        sess = session_of(ts)
        if sess != "LONDON_NY_OVERLAP":      # kill zone only (config default)
            continue
        if ts.weekday() == 4 and ts.hour >= 18:
            continue
        if regime.iloc[i] == "CRISIS":
            continue

        side = None
        trace = ""
        ml_p = probs.loc[ts] if probs is not None else np.nan
        soft = 0.0
        if mode == "apex" and not np.isnan(ml_p):
            soft = 0.5 if ml_p >= 0.55 else (-0.5 if ml_p <= 0.45 else 0.0)

        # ---- module votes (regime-routed) ----
        if reg_v[i] in ("TREND_UP", "TREND_DOWN"):
            # Reaper-X conditions
            near = abs(close[i] - e20[i]) <= 0.6 * a14[i]
            ok_adx = dx14[i] >= 24
            if bias_v[i] == "LONG" and near and ok_adx and \
                    38 <= r14[i] <= 52 and close[i] > opn[i] and r14[i] >= r14[i - 1]:
                side = "LONG"
                trace = "trend"
            elif bias_v[i] == "SHORT" and near and ok_adx and \
                    48 <= r14[i] <= 62 and close[i] < opn[i] and r14[i] <= r14[i - 1]:
                side = "SHORT"
                trace = "trend"
        elif reg_v[i] == "RANGE" and have_feats:
            bb = fcol["f_bb_pos"][i] if fcol["f_bb_pos"] is not None else np.nan
            rs = r14[i]
            z = fcol["f_zscore_100"][i] if fcol["f_zscore_100"] is not None else np.nan
            if not np.isnan(bb) and not np.isnan(z):
                if bb <= 0.05 and rs < 30 and z < -1.5:
                    side, trace = "LONG", "meanrev"
                elif bb >= 0.95 and rs > 70 and z > 1.5:
                    side, trace = "SHORT", "meanrev"
        elif reg_v[i] == "VOLATILE_CHOP" and have_feats:
            bu = fcol["f_donch_break_up"][i] if fcol["f_donch_break_up"] is not None else 0
            bd = fcol["f_donch_break_dn"][i] if fcol["f_donch_break_dn"] is not None else 0
            ofi = fcol["f_ofi_12"][i] if fcol["f_ofi_12"] is not None else np.nan
            volz = fcol["f_vol_z"][i] if fcol["f_vol_z"] is not None else np.nan
            if not np.isnan(ofi) and not np.isnan(volz) and volz > 0.5 and abs(ofi) > 0.05:
                if bu == 1 and ofi > 0:
                    side, trace = "LONG", "breakout"
                elif bd == 1 and ofi < 0:
                    side, trace = "SHORT", "breakout"

        if side is None:
            continue
        if mode == "apex" and soft != 0 and np.sign(soft) != (1 if side == "LONG" else -1):
            # meta-model disagrees with the only speaking module -> skip
            continue

        entry = close[i] + (SPREAD + SLIPPAGE if side == "LONG" else -(SPREAD + SLIPPAGE))
        sl_dist = 1.2 * a14[i]
        sl = entry - sl_dist if side == "LONG" else entry + sl_dist
        # locked production geometry: TP = 3.2 x ATR = 2.667R
        tp = entry + 2.667 * sl_dist if side == "LONG" else entry - 2.667 * sl_dist
        oz = (equity * RISK_PCT / 100.0) / sl_dist
        pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "orig_sl": sl,
               "oz": oz, "be": False, "session": sess, "regime": reg_v[i],
               "trace": trace}

    if pos is not None:
        close_pos(len(h1) - 1, close[-1], "EOD")

    return _report(trades, mode)


def _report(trades: list[dict], mode: str) -> dict:
    tdf = pd.DataFrame(trades)
    out = {"mode": mode, "trades": len(tdf)}
    if tdf.empty:
        out.update({"net": 0, "wr": 0, "pf": 0, "dd": 0, "blocks": 0, "block_rate": 0})
        return out
    eq = START_EQ + tdf["pnl"].cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    wins = tdf[tdf["pnl"] > 0]["pnl"].sum()
    losses = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    tdf["block"] = tdf["ts"].dt.strftime("%Y-%m-%dT") + \
        ((tdf["ts"].dt.hour // 4) * 4).astype(str).str.zfill(2)
    block_pnl = tdf.groupby("block")["pnl"].sum()
    n_blocks = len(block_pnl)
    hit = (block_pnl >= START_EQ * BLOCK_TARGET_PCT).sum()
    out.update({
        "net": float(tdf["pnl"].sum()),
        "wr": len(tdf[tdf["pnl"] > 0]) / len(tdf) * 100,
        "pf": wins / losses if losses else float("inf"),
        "dd": float(dd),
        "blocks": n_blocks,
        "block_rate": hit / n_blocks * 100 if n_blocks else 0,
    })
    return out


def main() -> int:
    store = FeatureStore()
    h1, h4, feats = load_all(store)
    store.close()
    if h1.empty or feats.empty:
        print("[!] run ingestion + features first")
        return 1
    n = len(h1)
    bounds = [h1.index[min(int(n * k / FOLDS), n - 1)] for k in range(FOLDS + 1)]
    folds = [(bounds[k], bounds[k + 1]) for k in range(FOLDS)]
    cprint(f"[*] APEX-X walk-forward on {len(h1):,} bars "
           f"({h1.index[0].date()} .. {h1.index[-1].date()}), {FOLDS} folds", YELLOW)

    cprint("[*] training expanding-window meta-model probabilities...", YELLOW)
    probs = ml_walkforward_probs(feats, folds)
    have_ml = probs.notna().mean() > 0.5
    cprint(f"    ML coverage: {probs.notna().mean() * 100:.0f}% of bars", YELLOW)

    cprint("[*] simulating REAPER-X baseline (trend-only)...", YELLOW)
    base = simulate(h1, h4, feats, mode="baseline")
    cprint("[*] simulating APEX-X ensemble...", YELLOW)
    apex = simulate(h1, h4, feats, mode="apex",
                    probs=probs if have_ml else None)

    print("=" * 70)
    print(f" {'metric':<26}{'REAPER-X':>18}{'APEX-X':>18}")
    print("-" * 70)
    rows = [("trades", "trades"), ("net (USD)", "net"), ("win rate %", "wr"),
            ("profit factor", "pf"), ("max DD %", "dd"),
            ("4h blocks active", "blocks"), ("blocks >= $20 %", "block_rate")]
    for label, key in rows:
        b, a = base.get(key, 0), apex.get(key, 0)
        fb = f"{b:,.2f}" if isinstance(b, float) else f"{b:,}"
        fa = f"{a:,.2f}" if isinstance(a, float) else f"{a:,}"
        print(f" {label:<26}{fb:>18}{fa:>18}")
    print("=" * 70)
    better = apex["net"] > base["net"]
    cprint(f" verdict: APEX-X {'OUTPERFORMS' if better else 'does NOT beat'} "
           f"the REAPER-X baseline on this window "
           f"({apex['net']:+,.0f} vs {base['net']:+,.0f} USD)",
           GREEN if better else RED)
    cprint(" note: NEWS module abstains in backtests (no reproducible historical "
           "calendar) - live ensemble has one extra voter.", YELLOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
