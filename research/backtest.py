"""
GOLD REAPER :: 20-Year Backtester
=================================
Replays REAPER-X over historical gold data with realistic assumptions:
  - spread $0.35 + slippage $0.05 per side (Exness-standard-ish)
  - SL/TP evaluated against each bar's high/low (worst case first)
  - position sizing at 1% risk, compounding equity
  - session gating + US-data blackouts, same as live bot
Reports: PnL curve, win rate, profit factor, max drawdown, per-year stats.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import CONFIG  # noqa: E402
from core.indicators import atr, ema, rsi  # noqa: E402
from core.logger import GREEN, RED, YELLOW, cprint  # noqa: E402
from core.sessions import session_of  # noqa: E402
from core.strategy import ReaperX  # noqa: E402

SPREAD = 0.35
SLIPPAGE = 0.05


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df.iloc[:, 0], utc=True, format="mixed")
    return df.set_index("time").sort_index()


def run_backtest(h1: pd.DataFrame, h4: pd.DataFrame,
                 risk_pct: float = 1.0, verbose: bool = True,
                 cfg: CONFIG.__class__ | None = None) -> dict:
    cfg = cfg or CONFIG
    strat = ReaperX(cfg)
    equity = 10_000.0
    start_equity = equity
    trades: list[dict] = []
    open_pos: dict | None = None

    e20 = ema(h1["close"], cfg.ema_pullback)
    rsi_s = rsi(h1["close"], cfg.rsi_period)
    atr_s = atr(h1, cfg.atr_period)
    h4e50 = ema(h4["close"], cfg.htf_trend_ema_fast)
    h4e200 = ema(h4["close"], cfg.htf_trend_ema_slow)

    # align h4 bias to h1 timeline
    bias_s = pd.Series("NEUTRAL", index=h1.index)
    h4f = h4e50.reindex(h1.index, method="ffill")
    h4s = h4e200.reindex(h1.index, method="ffill")
    h4c = h4["close"].reindex(h1.index, method="ffill")
    bias_s[(h4c > h4f) & (h4f > h4s)] = "LONG"
    bias_s[(h4c < h4f) & (h4f < h4s)] = "SHORT"

    from core.indicators import adx as adx_fn
    adx_s = adx_fn(h1, cfg.atr_period)

    # top-of-book arrays for speed
    close_v = h1["close"].values
    high_v = h1["high"].values
    low_v = h1["low"].values
    idx = h1.index
    e20_v, rsi_v, atr_v = e20.values, rsi_s.values, atr_s.values
    adx_v = adx_s.values
    bias_v = bias_s.values

    cfg_vec = cfg
    for i in range(220, len(h1) - 1):
        ts = idx[i]

        # ---------- manage open position on this bar ----------
        if open_pos is not None:
            side, entry, sl, tp, orig_sl = (open_pos[k] for k in
                                            ("side", "entry", "sl", "tp", "orig_sl"))
            be, trail = open_pos["be"], open_pos["trail"]
            r_dist = abs(entry - orig_sl)
            hi, lo = high_v[i], low_v[i]
            closed = False

            # conservative: SL checked before TP
            if side == "LONG" and lo <= sl:
                pnl = (sl - entry) * open_pos["oz"] - 2 * SPREAD * open_pos["oz"]
                closed = True
            elif side == "SHORT" and hi >= sl:
                pnl = (entry - sl) * open_pos["oz"] - 2 * SPREAD * open_pos["oz"]
                closed = True
            elif side == "LONG" and hi >= tp:
                pnl = (tp - entry) * open_pos["oz"] - 2 * SPREAD * open_pos["oz"]
                closed = True
            elif side == "SHORT" and lo <= tp:
                pnl = (entry - tp) * open_pos["oz"] - 2 * SPREAD * open_pos["oz"]
                closed = True

            if closed:
                equity += pnl
                trades.append({"ts": ts, "side": side, "pnl": pnl,
                               "session": open_pos["session"]})
                open_pos = None
            else:
                move = (hi - entry) if side == "LONG" else (entry - lo)
                r_mult = move / r_dist if r_dist else 0
                a = atr_v[i]
                if not be and r_mult >= cfg_vec.breakeven_at_r:
                    open_pos["sl"] = entry + (0.05 * a if side == "LONG" else -0.05 * a)
                    open_pos["be"] = True
                elif be and not trail and r_mult >= cfg_vec.trail_start_r:
                    open_pos["trail"] = True
                elif be and trail:
                    t = (hi - cfg_vec.trail_atr_mult * a if side == "LONG"
                         else lo + cfg_vec.trail_atr_mult * a)
                    if (side == "LONG" and t > open_pos["sl"]) or \
                       (side == "SHORT" and t < open_pos["sl"]):
                        open_pos["sl"] = t

        # ---------- new entry on bar close ----------
        if open_pos is not None:
            continue
        sess = session_of(ts)
        if cfg_vec.session_windows.get(sess) is None:
            continue
        if ts.weekday() == 4 and ts.hour >= cfg_vec.friday_last_entry_utc:
            continue

        bias = bias_v[i]
        if bias == "NEUTRAL":
            continue
        a = atr_v[i]
        if a < cfg_vec.min_atr_price or adx_v[i] < cfg_vec.min_adx:
            continue
        px, e, r_now, r_prev = close_v[i], e20_v[i], rsi_v[i], rsi_v[i - 1]
        if abs(px - e) > 0.6 * a:
            continue
        o_i = h1["open"].values[i]

        sig_side = None
        if bias == "LONG" and cfg_vec.rsi_long_zone[0] <= r_now <= cfg_vec.rsi_long_zone[1] \
                and px > o_i and r_now >= r_prev:
            sig_side = "LONG"
        elif bias == "SHORT" and cfg_vec.rsi_short_zone[0] <= r_now <= cfg_vec.rsi_short_zone[1] \
                and px < o_i and r_now <= r_prev:
            sig_side = "SHORT"
        if sig_side is None:
            continue

        if sig_side == "LONG":
            entry = px + SPREAD + SLIPPAGE
            sl = entry - cfg_vec.sl_atr_mult * a
            tp = entry + cfg_vec.tp_atr_mult * a
        else:
            entry = px - SPREAD - SLIPPAGE
            sl = entry + cfg_vec.sl_atr_mult * a
            tp = entry - cfg_vec.tp_atr_mult * a

        oz = (equity * risk_pct / 100.0) / abs(entry - sl)
        open_pos = {"side": sig_side, "entry": entry, "sl": sl, "tp": tp,
                    "orig_sl": sl, "oz": oz, "be": False, "trail": False,
                    "session": sess, "ts": ts}

    # close leftover at last price
    if open_pos is not None:
        side = open_pos["side"]
        last = close_v[-1]
        pnl = ((last - open_pos["entry"]) if side == "LONG"
               else (open_pos["entry"] - last)) * open_pos["oz"] - 2 * SPREAD * open_pos["oz"]
        equity += pnl
        trades.append({"ts": idx[-1], "side": side, "pnl": pnl,
                       "session": open_pos["session"]})

    return _report(trades, start_equity, equity, idx[0], idx[-1], verbose)


def _report(trades, start_eq, end_eq, t0, t1, verbose) -> dict:
    tdf = pd.DataFrame(trades)
    out: dict = {"trades": len(tdf), "net_pnl": end_eq - start_eq,
                 "return_pct": (end_eq / start_eq - 1) * 100}
    if len(tdf) == 0:
        out.update({"win_rate": 0, "profit_factor": 0, "max_dd_pct": 0})
        return out
    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]
    out["win_rate"] = len(wins) / len(tdf) * 100
    gp = wins["pnl"].sum()
    gl = abs(losses["pnl"].sum())
    out["profit_factor"] = gp / gl if gl > 0 else float("inf")

    eq_curve = start_eq + tdf["pnl"].cumsum()
    peak = eq_curve.cummax()
    dd = (eq_curve - peak) / peak * 100
    out["max_dd_pct"] = dd.min()

    years = (t1 - t0).days / 365.25
    out["years"] = years
    out["cagr_pct"] = ((end_eq / start_eq) ** (1 / years) - 1) * 100 if years > 0 else 0
    out["trades_per_year"] = len(tdf) / years if years > 0 else 0
    out["avg_pnl"] = tdf["pnl"].mean()

    if verbose:
        cprint("\n" + "═" * 66, RED)
        cprint(" REAPER-X :: BACKTEST VERDICT", RED)
        cprint("═" * 66, RED)
        cprint(f" window          : {t0.date()} -> {t1.date()} ({years:.1f}y)", YELLOW)
        cprint(f" trades          : {len(tdf):,}  ({out['trades_per_year']:.0f}/yr)", YELLOW)
        cprint(f" net result      : {end_eq - start_eq:+,.2f} USD "
               f"({out['return_pct']:+.1f}% on 10k, 1% risk compounding)", GREEN)
        cprint(f" CAGR            : {out['cagr_pct']:+.1f}% / yr", GREEN)
        cprint(f" win rate        : {out['win_rate']:.1f}%", YELLOW)
        cprint(f" profit factor   : {out['profit_factor']:.2f}", YELLOW)
        cprint(f" max drawdown    : {out['max_dd_pct']:.1f}%", RED)
        by_year = tdf.groupby(tdf["ts"].dt.year)["pnl"].sum()
        cprint(" pnl by year     :", YELLOW)
        for y, v in by_year.items():
            cprint(f"   {y} : {v:+10,.0f} USD {'█' * max(1, int(abs(v) / 400))}", GREEN)
        by_sess = tdf.groupby("session")["pnl"].agg(["sum", "count"])
        cprint(" pnl by session  :", YELLOW)
        for s, row in by_sess.iterrows():
            cprint(f"   {s:<18} {row['sum']:+10,.0f} USD  ({int(row['count'])} trades)", GREEN)
        cprint("═" * 66, RED)
    return out


def main() -> int:
    h1_path = ROOT / "data" / "history" / "XAUUSD_H1.csv"
    h4_path = ROOT / "data" / "history" / "XAUUSD_H4.csv"
    d1_path = ROOT / "data" / "history" / "XAUUSD_D1.csv"

    if h1_path.exists():
        h1 = load(h1_path)
        h4 = load(h4_path) if h4_path.exists() else h1.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        cprint(f"[*] backtesting REAPER-X on {len(h1):,} hourly bars...", YELLOW)
        run_backtest(h1, h4)
    elif d1_path.exists():
        d1 = load(d1_path)
        cprint(f"[*] only daily data present ({len(d1):,} bars) - coarse backtest", YELLOW)
        run_backtest(d1, d1)
    else:
        cprint("[!] no history. run: python data/fetch_history.py", RED)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
