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

Every rule that define the hunt is driven by a cfg dict (DEFAULT_CFG).
research/diagnose.py and research/tune_v3.py reuse this engine so any
config change published in the README is reproducible with one command.

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

# Locked production geometry (v2.2 defaults - reproduces published numbers).
# research/tune_v3.py searches neighbourhoods; only configs that improve the
# WORST fold get promoted here (see CHANGELOG).
DEFAULT_CFG: dict = {
    "hours": None,             # None -> overlap hours 12..15 via session_of
    "vol_band": None,          # (lo, hi) percentile bounds on normalized-ATR rank
    "time_stop_bars": None,    # exit at close if trade not at BE after N bars
    "tp_r": 2.667,             # TP distance in R (= 3.2 x ATR with SL 1.2 x ATR)
    "sl_atr": 1.2,             # SL distance in ATR multiples
    "be_r": 1.0,               # move stop to breakeven at +R
    "trail_r": 1.5,            # start ATR trail at +R
    "modules": ("trend", "meanrev", "breakout"),
    "meanrev_vol_max": None,    # meanrev only when ATR-rank < this (None = off)
    "sl_first": True,          # pessimistic: intra-bar SL is checked before TP
    "spread": SPREAD,
    "slippage": SLIPPAGE,
    "regime_allow": None,      # e.g. ("TREND_UP", "TREND_DOWN") restricts entries
}

# v2.3 hardened config (research/tune_v3.py, walk-forward: worst fold
# -663 -> -112, full-period net -970 -> +34 on 11 trades - sample too small
# to claim an edge; see docs/DIAGNOSIS.md). Live defaults mirror this.
V3_CFG: dict = {
    "hours": {12, 13},
    "tp_r": 2.0,
    "meanrev_vol_max": 0.4,
}


def merge_cfg(**over) -> dict:
    cfg = dict(DEFAULT_CFG)
    cfg.update(over)
    return cfg


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
             mode: str = "apex", probs: pd.Series | None = None,
             cfg: dict | None = None) -> list[dict]:
    """Replay the hunt. Returns the trade ledger (list of dicts)."""
    c = merge_cfg() if cfg is None else merge_cfg(**cfg)
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
    volp_v = vol_pct.values

    # feature dict accessors (fast column picks)
    fcol = ({c2: feats[c2].values if c2 in feats.columns else None for c2 in
             ("f_bb_pos", "f_rsi_14", "f_zscore_100", "f_donch_break_up",
              "f_donch_break_dn", "f_ofi_12", "f_vol_z")}
            if feats is not None else {})
    have_feats = feats is not None and len(feats) == len(h1)

    spread = float(c["spread"])
    slippage = float(c["slippage"])
    cost = spread + slippage
    modules = tuple(c["modules"])
    hours = c["hours"]
    vol_band = c["vol_band"]
    regime_allow = c["regime_allow"]

    equity = START_EQ
    trades: list[dict] = []
    pos = None

    def close_pos(i, price, why):
        nonlocal equity, pos
        if pos is None:
            return
        diff = (price - pos["entry"]) if pos["side"] == "LONG" else (pos["entry"] - price)
        pnl = diff * pos["oz"] - 2 * cost * pos["oz"]
        equity += pnl
        trades.append({"ts": idx[i], "side": pos["side"], "pnl": pnl,
                       "session": pos["session"], "regime": pos["regime"],
                       "trace": pos["trace"], "exit": why,
                       "entry_ts": pos["entry_ts"]})
        pos = None

    for i in range(220, len(h1) - 1):
        ts = idx[i]

        # ---- manage open ----
        if pos is not None:
            r_dist = abs(pos["entry"] - pos["orig_sl"])
            hi_, lo_ = high[i], low[i]
            hit_sl = (pos["side"] == "LONG" and lo_ <= pos["sl"]) or \
                     (pos["side"] == "SHORT" and hi_ >= pos["sl"])
            hit_tp = (pos["side"] == "LONG" and hi_ >= pos["tp"]) or \
                     (pos["side"] == "SHORT" and lo_ <= pos["tp"])
            if hit_sl and c["sl_first"]:
                close_pos(i, pos["sl"], "SL")
            elif hit_tp:
                close_pos(i, pos["tp"], "TP")
            elif hit_sl:
                close_pos(i, pos["sl"], "SL")
            else:
                move = (hi_ - pos["entry"]) if pos["side"] == "LONG" else (pos["entry"] - lo_)
                rm = move / r_dist if r_dist else 0
                if not pos["be"] and rm >= c["be_r"]:
                    pos["sl"] = pos["entry"] + (0.05 * a14[i] if pos["side"] == "LONG"
                                                else -0.05 * a14[i])
                    pos["be"] = True
                elif pos["be"] and rm >= c["trail_r"]:
                    trail = (hi_ - 1.2 * a14[i] if pos["side"] == "LONG"
                             else lo_ + 1.2 * a14[i])
                    if (pos["side"] == "LONG" and trail > pos["sl"]) or \
                       (pos["side"] == "SHORT" and trail < pos["sl"]):
                        pos["sl"] = trail
                # time-stop: dead weight exit
                if pos is not None and c["time_stop_bars"] and not pos["be"] and \
                        (i - pos["i0"]) >= c["time_stop_bars"]:
                    close_pos(i, close[i], "TIME")
            continue

        # ---- entry gates ----
        sess = session_of(ts)
        if sess != "LONDON_NY_OVERLAP":      # kill zone only (config default)
            continue
        if hours is not None and ts.hour not in hours:
            continue
        if ts.weekday() == 4 and ts.hour >= 18:
            continue
        if regime.iloc[i] == "CRISIS":
            continue
        if regime_allow is not None and reg_v[i] not in regime_allow:
            continue
        if vol_band is not None:
            vp = volp_v[i]
            if np.isnan(vp) or vp < vol_band[0] or vp > vol_band[1]:
                continue

        side = None
        trace = ""
        ml_p = probs.loc[ts] if probs is not None else np.nan
        soft = 0.0
        if mode == "apex" and not np.isnan(ml_p):
            soft = 0.5 if ml_p >= 0.55 else (-0.5 if ml_p <= 0.45 else 0.0)

        # ---- module votes (regime-routed) ----
        if reg_v[i] in ("TREND_UP", "TREND_DOWN") and "trend" in modules:
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
        elif reg_v[i] == "RANGE" and have_feats and "meanrev" in modules:
            mvm = c["meanrev_vol_max"]
            vol_ok = True
            if mvm is not None:
                vp_m = volp_v[i]
                vol_ok = (not np.isnan(vp_m)) and vp_m < mvm
            bb = fcol["f_bb_pos"][i] if fcol["f_bb_pos"] is not None else np.nan
            rs = r14[i]
            z = fcol["f_zscore_100"][i] if fcol["f_zscore_100"] is not None else np.nan
            if vol_ok and not np.isnan(bb) and not np.isnan(z):
                if bb <= 0.05 and rs < 30 and z < -1.5:
                    side, trace = "LONG", "meanrev"
                elif bb >= 0.95 and rs > 70 and z > 1.5:
                    side, trace = "SHORT", "meanrev"
        elif reg_v[i] == "VOLATILE_CHOP" and have_feats and "breakout" in modules:
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

        entry = close[i] + (cost if side == "LONG" else -cost)
        sl_dist = c["sl_atr"] * a14[i]
        sl = entry - sl_dist if side == "LONG" else entry + sl_dist
        tp = entry + c["tp_r"] * sl_dist if side == "LONG" else entry - c["tp_r"] * sl_dist
        oz = (equity * RISK_PCT / 100.0) / sl_dist
        pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "orig_sl": sl,
               "oz": oz, "be": False, "session": sess, "regime": reg_v[i],
               "trace": trace, "entry_ts": ts, "i0": i}

    if pos is not None:
        close_pos(len(h1) - 1, close[-1], "EOD")

    return trades


def stats_from(trades: list[dict]) -> dict:
    tdf = pd.DataFrame(trades)
    out = {"trades": len(tdf)}
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


def fold_nets(trades: list[dict], folds: list[tuple]) -> list[float]:
    tdf = pd.DataFrame(trades)
    nets = []
    for lo, hi in folds:
        if tdf.empty:
            nets.append(0.0)
            continue
        m = (tdf["ts"] >= lo) & (tdf["ts"] < hi)
        nets.append(float(tdf[m]["pnl"].sum()))
    return nets


def _report(trades: list[dict], mode: str) -> dict:
    return stats_from(trades)


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

    cprint("[*] simulating REAPER-X (trend module only)...", YELLOW)
    base_t = simulate(h1, h4, feats, mode="baseline", cfg={"modules": ("trend",)})
    cprint("[*] simulating ENSEMBLE (all modules, no ML)...", YELLOW)
    ens_t = simulate(h1, h4, feats, mode="baseline")
    cprint("[*] simulating APEX-X (ensemble + ML soft vote)...", YELLOW)
    apex_t = simulate(h1, h4, feats, mode="apex", probs=probs if have_ml else None)
    cprint("[*] simulating v3 hardened config (ensemble, V3 gates)...", YELLOW)
    v3_t = simulate(h1, h4, feats, mode="baseline", cfg=V3_CFG)
    cprint("[*] simulating APEX-X v3 (V3 gates + ML soft vote)...", YELLOW)
    v3m_t = simulate(h1, h4, feats, mode="apex", cfg=V3_CFG,
                     probs=probs if have_ml else None)

    runs = [("REAPER-X (trend)", base_t), ("ENSEMBLE (no ML)", ens_t),
            ("APEX-X (+ML)", apex_t), ("V3 hardened", v3_t),
            ("APEX-X v3 (+ML)", v3m_t)]
    st = {name: stats_from(t) for name, t in runs}
    fn = {name: fold_nets(t, folds) for name, t in runs}

    print("=" * 106)
    print(f" {'metric':<24}" + "".join(f"{n:>16}" for n, _ in runs))
    print("-" * 106)
    rows = [("trades", "trades"), ("net (USD)", "net"), ("win rate %", "wr"),
            ("profit factor", "pf"), ("max DD %", "dd"),
            ("4h blocks active", "blocks"), ("blocks >= $20 %", "block_rate")]
    for label, key in rows:
        cells = []
        for name, _ in runs:
            v = st[name].get(key, 0)
            cells.append(f"{v:,.2f}" if isinstance(v, float) else f"{v:,}")
        print(f" {label:<24}" + "".join(f"{c:>16}" for c in cells))
    print("-" * 106)
    for name, _ in runs:
        print(f" {'fold nets':<24}" + "".join(f"{v:>+16.0f}" for v in fn[name])
              + f"   {name}")
    print("=" * 106)
    better = st["APEX-X (+ML)"]["net"] > st["ENSEMBLE (no ML)"]["net"]
    cprint(f" verdict: ML soft vote {'reduces' if better else 'increases'} damage "
           f"({st['APEX-X (+ML)']['net']:+,.0f} vs {st['ENSEMBLE (no ML)']['net']:+,.0f} USD). "
           f"v3 hardened: {st['V3 hardened']['net']:+,.0f} full / "
           f"worst fold {min(fn['V3 hardened']):+,.0f} on "
           f"{st['V3 hardened']['trades']} trades - damage reduction, NOT proof "
           f"of edge (tiny sample). See docs/DIAGNOSIS.md.",
           GREEN if st['V3 hardened']['net'] > 0 else RED)
    cprint(" note: NEWS module abstains in backtests (no reproducible historical "
           "calendar) - live ensemble has one extra voter.", YELLOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
