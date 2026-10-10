"""
GOLD REAPER HPE :: Regime / Session / Hour Report — publish everything
=======================================================================
Reads data/hpe_report.json (from research/backtest_hpe.py) and prints +
saves the honest breakdown the HPE contract demands:

  - win rate by regime
  - win rate by session
  - win rate by entry hour
  - win rate by setup signature (which modules carried the vote)
  - fold table including every losing fold

Run:  python research/regime_report.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "data" / "hpe_report.json"
OUT = ROOT / "data" / "regime_report.json"
START_EQ = 10_000.0


def _wr_table(trades: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        groups.setdefault(str(t.get(key, "?")), []).append(t)
    out = {}
    for g, ts in sorted(groups.items()):
        wins = [t for t in ts if t["pnl"] > 0]
        net = sum(t["pnl"] for t in ts)
        losses = abs(sum(t["pnl"] for t in ts if t["pnl"] <= 0))
        out[g] = {"trades": len(ts),
                  "win_rate_pct": round(100.0 * len(wins) / len(ts), 1),
                  "net": round(net, 2),
                  "pf": round(sum(t["pnl"] for t in wins) / losses, 2)
                  if losses else None}
    return out


def setup_signature(t: dict) -> str:
    """Modules that voted the trade's side, strongest first."""
    side_dir = 1 if t["side"] == "LONG" else -1
    votes = t.get("votes") or {}
    carriers = [f"{name}({v.get('strength', 0):.1f})"
                for name, v in votes.items() if v.get("dir") == side_dir]
    return "+".join(sorted(carriers)) if carriers else "none"


def main() -> int:
    if not REPORT.exists():
        print("[!] no hpe_report.json - run research/backtest_hpe.py first")
        return 1
    rep = json.loads(REPORT.read_text())
    trades = rep.get("trades", [])
    print("=" * 88)
    print(" GOLD REAPER HPE :: regime / session / hour report "
          f"({len(trades)} trades)")
    print("=" * 88)
    for t in trades:
        t["hour"] = pd.Timestamp(t["entry_ts"]).hour
        t["setup"] = setup_signature(t)

    by_regime = _wr_table(trades, "regime")
    by_session = _wr_table(trades, "session")
    by_hour = _wr_table(trades, "hour")
    by_fold = _wr_table(trades, "fold")
    by_setup = _wr_table(trades, "setup")

    for name, tbl in (("REGIME", by_regime), ("SESSION", by_session),
                      ("HOUR (UTC)", by_hour), ("FOLD", by_fold)):
        print(f"\n -- win rate by {name} " + "-" * (88 - 22 - len(name)))
        print(f" {'bucket':<28} {'trades':>7} {'wr %':>7} {'net $':>10} {'pf':>8}")
        for g, v in tbl.items():
            pf = f"{v['pf']:.2f}" if v["pf"] is not None else "inf"
            print(f" {g:<28} {v['trades']:>7} {v['win_rate_pct']:>7.1f} "
                  f"{v['net']:>+10.2f} {pf:>8}")

    print("\n -- setup signatures (top 8 by count) " + "-" * 46)
    print(f" {'modules carrying the vote':<52} {'n':>4} {'wr %':>7} {'net $':>10}")
    common = Counter(t["setup"] for t in trades).most_common(8)
    for sig, _n in common:
        v = by_setup[sig]
        print(f" {sig:<52} {v['trades']:>4} {v['win_rate_pct']:>7.1f} "
              f"{v['net']:>+10.2f}")

    stats = rep.get("stats", {})
    print("-" * 88)
    print(f" overall: {stats.get('trades')} trades | net ${stats.get('net', 0):+,.2f}"
          f" | wr {stats.get('wr', 0):.1f}% | PF {stats.get('pf', 0):.2f} | "
          f"DD {stats.get('dd', 0):.1f}%")
    print(f" armed  : {rep.get('armed')} (worst fold PF "
          f"{rep.get('worst_fold_pf')}, net {rep.get('worst_fold_net'):+,.0f})")
    print(" honesty: every fold above is published - losing folds included.")
    print("=" * 88)

    OUT.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "by_regime": by_regime, "by_session": by_session,
        "by_hour": by_hour, "by_fold": by_fold, "by_setup": by_setup,
        "overall": stats, "armed": rep.get("armed"),
    }, indent=2))
    print(f" saved  : {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
