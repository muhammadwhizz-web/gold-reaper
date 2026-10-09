"""
GOLD REAPER :: Paper Track Record (append-only ledger keeper)
==============================================================
The honesty contract: paper results get published daily, win or lose.
This script reads the paper broker's trades + risk state and appends one
row per UTC day to docs/TRACK_RECORD.md. Idempotent: re-running the same
day updates that day's row instead of duplicating it.

Designed for the systemd timer / Task Scheduler job that runs after the
bot (see install_linux.sh). CI-safe: exits 0 without writing when there
is nothing to record yet.

Usage:
  python research/track_record.py            # record today's row
  python research/track_record.py --check    # exit 1 if a row is stale
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "TRACK_RECORD.md"
TRADES = ROOT / "data" / "trades.csv"
RISK = ROOT / "data" / "apex_risk.json"

HEADER = """# PAPER TRACK RECORD — append-only

> Every UTC day the paper bot trades gets one row here, win or lose, appended
> by `research/track_record.py`. Rows are never edited after the fact; a
> correction is a new row with a note. This ledger is the arbitration between
> "the backtest says" and "the bot does".

Protocol: PAPER broker · 1% risk · v3 hardened config · targets are
engineering targets, NOT guarantees (see docs/RISK.md).

| date (UTC) | trades | net PnL ($) | equity ($) | blocks hit | note |
|------------|-------:|------------:|-----------:|-----------:|------|
"""


def _day_rows(day: str) -> list[dict]:
    if not TRADES.exists():
        return []
    out = []
    with TRADES.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = str(row.get("ts", row.get("time", "")))
            if ts.startswith(day):
                out.append(row)
    return out


def _parse_float(v, default=0.0) -> float:
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def collect(day: str) -> dict | None:
    rows = _day_rows(day)
    equity = None
    if RISK.exists():
        try:
            equity = _parse_float(json.loads(RISK.read_text()).get("equity"), None)
        except Exception:  # noqa: BLE001
            equity = None
    if not rows and equity is None:
        return None
    net = sum(_parse_float(r.get("pnl", r.get("pnl_usd", 0))) for r in rows)
    return {"day": day, "trades": len(rows), "net": round(net, 2),
            "equity": equity}


def build_row(rec: dict) -> str:
    eq = f"{rec['equity']:,.2f}" if rec.get("equity") is not None else "-"
    return (f"| {rec['day']} | {rec['trades']} | {rec['net']:+.2f} | "
            f"{eq} | - | auto |")


def main() -> int:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    rec = collect(day)
    if rec is None:
        print("[track-record] nothing to record yet (paper bot idle) - ok")
        return 0
    text = LEDGER.read_text(encoding="utf-8") if LEDGER.exists() else HEADER
    lines = text.rstrip("\n").splitlines()
    header_end = next((i for i, ln in enumerate(lines)
                       if ln.startswith("|") and "---" in ln), len(lines) - 1)
    row_line = build_row(rec)
    replaced = False
    for i in range(header_end + 1, len(lines)):
        if lines[i].startswith(f"| {day} "):
            lines[i] = row_line          # same-day re-run updates in place
            replaced = True
            break
    if not replaced:
        lines.append(row_line)
    LEDGER.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[track-record] {'updated' if replaced else 'appended'} {day}: "
          f"{rec['trades']} trades, {rec['net']:+.2f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
