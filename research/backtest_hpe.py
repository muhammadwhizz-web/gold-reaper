"""
GOLD REAPER HPE :: Walk-Forward Ensemble Backtest — the honesty instrument
===========================================================================
Replays the full HPE stack over H1 gold with ZERO leakage:

  - ensemble votes (8 modules) on every candidate bar, exactly as live
  - ML probabilities come ONLY from the expanding-window purged walk-forward
    (ml.train_hpe.walkforward) - fold 0 has no ML (honest cold start)
  - psychology + micro-structure frames precomputed no-lookahead
  - cross-asset module fed from features_1h (f_resid_z_*) when present,
    abstains honestly when not
  - NEWS module abstains (no reproducible historical sentiment - documented)
  - conservative fills: spread + slippage both sides, SL checked first,
    same-bar both-hit resolves SL-first
  - survival layer (core.risk_hpe) gates every entry and scales size
  - geometry: SL 1.2 x ATR, TP 2.2R, BE +1R, trail +1.5R (HPE band)

Output: data/hpe_report.json (ledger + folds + verdict). The verdict is
honest: if the worst fold PF < 1.2 the HPE ships DISARMED (soft/shadow
only) - losing folds are published, never hidden.

Run:  python research/backtest_hpe.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ensemble_hpe import EnsembleHPE  # noqa: E402
from core.indicators import adx, atr, ema, rsi  # noqa: E402
from core.logger import GREEN, RED, YELLOW, cprint  # noqa: E402
from core.psychology import PsychologyEngine  # noqa: E402
from core.regime import RegimeDetector  # noqa: E402
from core.risk_hpe import RiskHPE, RiskHPEConfig  # noqa: E402
from core.sessions import session_of  # noqa: E402
from core.strategy_hpe import HPEConfig  # noqa: E402
from features.micro_features import compute_micro_features  # noqa: E402
from features.store import FeatureStore  # noqa: E402
from ml.train_hpe import assemble_dataset, purged_walkforward, walkforward  # noqa: E402

SPREAD = 0.35
SLIPPAGE = 0.05
START_EQ = 10_000.0
TIME_STOP_BARS = 48          # 2 days flat -> exit at close (dead weight)
REPORT = ROOT / "data" / "hpe_report.json"
REPORT_TAG = os.environ.get("HPE_REPORT_TAG", "")   # variant runs, e.g. "_hours8"
if REPORT_TAG:
    REPORT = ROOT / "data" / f"hpe_report{REPORT_TAG}.json"


def load_all(store: FeatureStore):
    h1 = store.read_bars("1h")
    h4 = store.read_bars("4h")
    return h1, h4


def cross_map(feats: pd.DataFrame) -> dict[str, pd.Series]:
    """Pick the strongest available cross-asset anchor (DXY, else SILVER)."""
    out: dict[str, pd.Series] = {}
    for name in ("DXY", "SILVER"):
        rz = f"f_resid_z_{name}"
        co = f"f_corr_{name}"
        if rz in feats.columns:
            out["f_resid_z_" + name] = feats[rz]
        if co in feats.columns:
            out["f_corr_" + name] = feats[co]
        if out:
            break
    return out


def simulate(h1: pd.DataFrame, h4: pd.DataFrame, feats: pd.DataFrame,
             probs: pd.Series, hcfg: HPEConfig,
             folds: list[tuple]) -> tuple[list[dict], list[dict]]:
    """Full HPE replay. Returns (trade ledger, per-fold fold-info)."""
    c = h1["close"]
    a_s = atr(h1, 14)
    r_s = rsi(c, 14)
    dx_s = adx(h1, 14)
    e20_s = ema(c, 20)
    bb_m = c.rolling(20).mean()
    bb_sd = c.rolling(20).std()
    bb_up, bb_lo = bb_m + 2 * bb_sd, bb_m - 2 * bb_sd
    bb_w = (bb_up - bb_lo).replace(0, np.nan)
    z100_s = (c - c.rolling(100).mean()) / c.rolling(100).std().replace(0, np.nan)
    hh20 = h1["high"].rolling(20).max().shift(1)
    ll20 = h1["low"].rolling(20).min().shift(1)
    up_v = h1["volume"].where(h1["close"] >= h1["open"], 0.0).rolling(12).sum()
    dn_v = h1["volume"].where(h1["close"] < h1["open"], 0.0).rolling(12).sum()
    ofi_s = (up_v - dn_v) / (up_v + dn_v).replace(0, np.nan)
    vol_z_s = (h1["volume"] - h1["volume"].rolling(100).mean()) / \
        h1["volume"].rolling(100).std().replace(0, np.nan)

    # h4 bias (same definition as the frozen engine)
    # h4 bias — CAUSAL: 4h bars are open-stamped; without shift(1) an h1
    # entry inside a 4h block was served that block's FINAL close (up to
    # ~4h of future data). shift(1) serves the last COMPLETED 4h bar.
    f50 = ema(h4["close"], 50).shift(1).reindex(h1.index, method="ffill")
    f200 = ema(h4["close"], 200).shift(1).reindex(h1.index, method="ffill")
    h4c = h4["close"].shift(1).reindex(h1.index, method="ffill")
    bias = pd.Series("NEUTRAL", index=h1.index)
    bias[(h4c > f50) & (f50 > f200)] = "LONG"
    bias[(h4c < f50) & (f50 < f200)] = "SHORT"

    regime = RegimeDetector().classify_frame(h1)
    micro = compute_micro_features(h1)
    psy = PsychologyEngine().compute_frame(h1).drop(columns=["psy_regime_tag"])

    cx = cross_map(feats)
    cx_cols = {k: (v.reindex(h1.index).values if k in feats.columns else None)
               for k, v in cx.items()}

    close, opn = c.values, h1["open"].values
    high, low = h1["high"].values, h1["low"].values
    idx = h1.index
    rsi_v, rsi_prev = r_s.values, np.roll(r_s.values, 1)
    dx_v, a_v, e20_v = dx_s.values, a_s.values, e20_s.values
    bb_v, z_v = (c - bb_lo).values / bb_w.values, z100_s.values
    du_v = (c > hh20).astype(float).values
    dd_v = (c < ll20).astype(float).values
    ofi_v, volz_v = ofi_s.values, vol_z_s.values
    bias_v, reg_v = bias.values, regime.values
    micro_rows = micro.to_dict("records")
    psy_rows = psy.to_dict("records")
    prob_v = probs.reindex(h1.index).values if probs is not None \
        else np.full(len(h1), np.nan)

    cost = SPREAD + SLIPPAGE
    slippage = SLIPPAGE   # exit fee/oz, live PaperBroker parity
    ens = EnsembleHPE(psychology=PsychologyEngine(),
                      min_agreement=hcfg.min_agreement,
                      min_confidence=hcfg.min_confidence, ml_hard=hcfg.ml_hard)
    risk = RiskHPE(RiskHPEConfig(), starting_equity=START_EQ)

    equity = START_EQ
    trades: list[dict] = []
    pos: dict | None = None

    def close_pos(i: int, price: float, why: str) -> None:
        nonlocal equity, pos
        if pos is None:
            return
        diff = (price - pos["entry"]) if pos["side"] == "LONG" \
            else (pos["entry"] - price)
        # exit fee = SLIPPAGE/oz (live PaperBroker parity; entry pays
        # SPREAD+SLIPPAGE inside the fill)
        pnl = diff * pos["oz"] - slippage * pos["oz"]
        equity += pnl
        risk.record_close(pnl, idx[i])
        trades.append({
            "ts": idx[i].isoformat(), "entry_ts": pos["entry_ts"].isoformat(),
            "side": pos["side"], "pnl": pnl, "exit": why,
            "entry": pos["entry"], "sl": pos["orig_sl"], "tp": pos["tp"],
            "confidence": pos["confidence"], "risk_pct": pos["risk_pct"],
            "aligned": pos["aligned"], "session": pos["session"],
            "regime": pos["regime"], "votes": pos["votes"],
            "fold": next((k for k, (lo, hi) in enumerate(folds)
                          if pd.Timestamp(pos["entry_ts"]) >= lo
                          and pd.Timestamp(pos["entry_ts"]) < hi), 0),
        })
        pos = None

    for i in range(300, len(h1) - 1):
        ts = idx[i]

        # ---- manage open position first (conservative SL-first) ----------
        if pos is not None:
            hi_, lo_ = high[i], low[i]
            hit_sl = (pos["side"] == "LONG" and lo_ <= pos["sl"]) or \
                     (pos["side"] == "SHORT" and hi_ >= pos["sl"])
            hit_tp = (pos["side"] == "LONG" and hi_ >= pos["tp"]) or \
                     (pos["side"] == "SHORT" and lo_ <= pos["tp"])
            if hit_sl:
                close_pos(i, pos["sl"], "SL")
            elif hit_tp:
                close_pos(i, pos["tp"], "TP")
            else:
                r_dist = abs(pos["entry"] - pos["orig_sl"])
                move = (hi_ - pos["entry"]) if pos["side"] == "LONG" \
                    else (pos["entry"] - lo_)
                rm = move / r_dist if r_dist else 0.0
                if not pos["be"] and rm >= hcfg.breakeven_at_r:
                    pos["sl"] = pos["entry"] + (0.05 * a_v[i]
                                                if pos["side"] == "LONG"
                                                else -0.05 * a_v[i])
                    pos["be"] = True
                elif pos["be"] and rm >= hcfg.trail_start_r:
                    trail = (hi_ - hcfg.trail_atr_mult * a_v[i]
                             if pos["side"] == "LONG"
                             else lo_ + hcfg.trail_atr_mult * a_v[i])
                    if (pos["side"] == "LONG" and trail > pos["sl"]) or \
                       (pos["side"] == "SHORT" and trail < pos["sl"]):
                        pos["sl"] = trail
                        pos["trail"] = True
                if (i - pos["i0"]) >= TIME_STOP_BARS and not pos["be"]:
                    close_pos(i, close[i], "TIME")
            continue

        # ---- entry gates --------------------------------------------------
        sess = session_of(ts)
        if ts.hour not in hcfg.entry_hours_utc:
            continue
        if not hcfg.hours_explicit and sess != "LONDON_NY_OVERLAP":
            continue
        if ts.weekday() == 4 and ts.hour >= 18:
            continue
        if reg_v[i] == "CRISIS":
            continue
        if np.isnan(a_v[i]) or a_v[i] <= 0:
            continue
        ok, _why = risk.allow_entry(ts, equity=equity)
        if not ok:
            continue

        cross = {k: (v[i] if v is not None else np.nan)
                 for k, v in cx_cols.items()}
        p = prob_v[i]
        ctx = {
            "ts": ts, "close": close[i], "open": opn[i], "high": high[i],
            "low": low[i],
            "ind": {"rsi": rsi_v[i], "rsi_prev": rsi_prev[i], "adx": dx_v[i],
                    "atr": a_v[i], "e20": e20_v[i], "bb_pos": bb_v[i],
                    "z100": z_v[i], "donch_up": du_v[i], "donch_dn": dd_v[i],
                    "ofi": ofi_v[i], "vol_z": volz_v[i]},
            "h4_bias": bias_v[i], "regime": reg_v[i], "session": sess,
            "psy_row": pd.Series(psy_rows[i]), "micro_row": micro_rows[i],
            "cross": cross, "news": {"sent": 0.0, "conf": 0.0,
                                     "min_to_event": 9999.0, "relevance": 0.0},
            "ml_prob": None if np.isnan(p) else float(p),
        }
        d = ens.decide(ctx)
        if not d.accepted or d.side is None:
            continue

        entry = close[i] + (cost if d.side == "LONG" else -cost)
        sl_dist = hcfg.sl_atr_mult * a_v[i]
        sl = entry - sl_dist if d.side == "LONG" else entry + sl_dist
        tp = entry + hcfg.tp_r * sl_dist if d.side == "LONG" \
            else entry - hcfg.tp_r * sl_dist
        risk_pct = risk.risk_pct(d.confidence, d.regime)
        if risk_pct <= 0:
            continue
        oz = (equity * risk_pct / 100.0) / sl_dist
        pos = {"side": d.side, "entry": entry, "sl": sl, "tp": tp,
               "orig_sl": sl, "oz": oz, "be": False, "trail": False,
               "confidence": d.confidence, "risk_pct": risk_pct,
               "aligned": d.aligned, "session": sess, "regime": d.regime,
               "votes": d.votes, "entry_ts": ts, "i0": i}

    if pos is not None:
        close_pos(len(h1) - 1, close[-1], "EOD")
    return trades, []


def stats_from(trades: list[dict]) -> dict:
    tdf = pd.DataFrame(trades)
    out = {"trades": len(tdf)}
    if tdf.empty:
        return {**out, "net": 0.0, "wr": 0.0, "pf": 0.0, "dd": 0.0}
    eq = START_EQ + tdf["pnl"].cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax() * 100).min())
    wins = tdf[tdf["pnl"] > 0]["pnl"].sum()
    losses = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    out.update({
        "net": float(tdf["pnl"].sum()),
        "wr": float(len(tdf[tdf["pnl"] > 0]) / len(tdf) * 100),
        "pf": float(wins / losses) if losses else float("inf"),
        "dd": dd,
    })
    return out


def fold_nets(trades: list[dict], folds: list[tuple]) -> list[float]:
    nets = []
    for lo, hi in folds:
        m = [t for t in trades if lo <= pd.Timestamp(t["entry_ts"]) < hi]
        nets.append(float(sum(t["pnl"] for t in m)))
    return nets


def main() -> int:
    print("=" * 96)
    print(" GOLD REAPER HPE :: walk-forward ensemble backtest"
          f"{REPORT_TAG or ' (spec default)'}")
    print("=" * 96)
    store = FeatureStore()
    h1, h4 = load_all(store)
    if h1.empty or h4.empty:
        print("[!] no bars - run ingestion first")
        return 1
    cprint(f"[*] assembling dataset (f_* + mf_* + psy_*) over {len(h1):,} bars...",
           YELLOW)
    feats = assemble_dataset(store)
    store.close()
    folds_idx = purged_walkforward(feats)
    # purged_walkforward returns (train_idx, test_idx) POSITION ARRAYS -
    # the old code indexed feats.index with both and produced
    # DatetimeIndex objects, so every ``lo <= ts < hi`` comparison raised
    # ValueError the moment the sim produced its first trade (masked
    # while the DISARMED report contained zero trades).
    folds_ts = []
    for _tr, te in folds_idx:
        lo_t = feats.index[int(te[0])]
        hi_t = feats.index[int(te[-1])] + pd.Timedelta(hours=1)
        folds_ts.append((lo_t, hi_t))

    cprint("[*] expanding-window ML probabilities (purged + embargoed)...",
           YELLOW)
    probs, ml_reports, _eng, _art = walkforward(feats, folds_idx)
    cov = float(probs.notna().mean() * 100)
    cprint(f"    ML coverage {cov:.0f}% of bars (fold 0 = honest cold start)",
           YELLOW)

    cprint("[*] simulating HPE ensemble with survival risk layer...", YELLOW)
    hcfg = HPEConfig.from_env()
    trades, _ = simulate(h1, h4, feats, probs, hcfg, folds_ts)

    st = stats_from(trades)
    fn = fold_nets(trades, folds_ts)
    fold_wr = []
    for lo, hi in folds_ts:
        m = [t for t in trades if lo <= pd.Timestamp(t["entry_ts"]) < hi]
        w = len([t for t in m if t["pnl"] > 0])
        fold_wr.append(round(100.0 * w / len(m), 1) if m else None)

    print("-" * 96)
    print(f" entry hours : {sorted(hcfg.entry_hours_utc)}")
    print(f" trades {st['trades']} | net ${st['net']:+,.2f} | "
          f"win rate {st['wr']:.1f}% | PF {st['pf']:.2f} | max DD {st['dd']:.1f}%")
    print(" fold nets : " + " ".join(f"{v:+9.0f}" for v in fn))
    print(" fold WRs  : " + " ".join(f"{('%s%%' % w) if w is not None else '   -':>10}"
                                      for w in fold_wr))
    worst_fold_net = min(fn) if fn else 0.0
    worst_fold_pf = None
    for k, (lo, hi) in enumerate(folds_ts):
        m = [t for t in trades if lo <= pd.Timestamp(t["entry_ts"]) < hi]
        wins = sum(t["pnl"] for t in m if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in m if t["pnl"] <= 0))
        pf = wins / losses if losses else (float("inf") if wins else 0.0)
        worst_fold_pf = pf if worst_fold_pf is None else min(worst_fold_pf, pf)
    gate_pf = worst_fold_pf is not None and worst_fold_pf >= 1.2
    armed = bool(gate_pf and worst_fold_net > 0 and st["net"] > 0)
    verdict_txt = "worst fold survives the gate" if armed else \
        "ships as SHADOW/soft only; losing folds above are published, not hidden"
    print(f" worst fold: net {worst_fold_net:+,.0f} | PF "
          f"{worst_fold_pf if worst_fold_pf is not None else float('nan'):.2f} "
          f"-> {'PASS' if gate_pf else 'FAIL'} (gate >= 1.2)")
    print("=" * 96)
    cprint(f" VERDICT: HPE {'ARMED' if armed else 'DISARMED'} - {verdict_txt}",
           GREEN if armed else RED)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "bars": len(h1), "span": [str(h1.index[0]), str(h1.index[-1])],
        "ml_coverage_pct": cov, "ml_folds": ml_reports,
        "config": {"tp_r": hcfg.tp_r, "sl_atr": hcfg.sl_atr_mult,
                   "hours": list(hcfg.entry_hours_utc),
                   "min_agreement": hcfg.min_agreement,
                   "min_confidence": hcfg.min_confidence},
        "stats": st, "fold_nets": fn, "fold_wr": fold_wr,
        "worst_fold_net": worst_fold_net, "worst_fold_pf": worst_fold_pf,
        "armed": armed, "trades": trades,
    }, indent=2, default=str))
    print(f" report  : {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
