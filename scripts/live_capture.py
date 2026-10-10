#!/usr/bin/env python3
"""LIVE-DATA VALIDATION sidecar (Phase 9).

Runs beside the paper bot and appends, append-only, into logs/live_validation/:
  bars_1h.csv      - every raw h1 bar as it lands in the store (dedup by ts)
  heartbeat.csv    - heartbeat snapshots (liveness + mode + broker labels)
  audit_tail.jsonl - the bot's decision audit stream (copied on rotate)
No synthesis, no interpolation: only rows read from the real store/files.

The bot holds the duckdb write lock (single-writer), so bars are read
from a db+wal snapshot copy. logs/ is gitignored: this is runtime
evidence, not source.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "logs" / "live_validation"
BARS = OUT / "bars_1h.csv"
HB = OUT / "heartbeat.csv"
AUDIT_MIRROR = OUT / "audit_tail.jsonl"

BAR_COLS = ["time", "open", "high", "low", "close", "volume"]


def _snapshot_bars() -> pd.DataFrame | None:
    """Read bars from a COPY of the store (the bot holds the write lock)."""
    from features.store import FeatureStore

    src = ROOT / "data" / "store" / "apex.duckdb"
    wal = src.with_suffix(".duckdb.wal")
    if not src.exists():
        return None
    tmpdir = Path(tempfile.mkdtemp(prefix="gr_capture_"))
    dst = tmpdir / "apex.duckdb"
    try:
        shutil.copyfile(src, dst)
        if wal.exists():
            shutil.copyfile(wal, dst.with_suffix(".duckdb.wal"))
        store = FeatureStore(path=dst)
        try:
            return store.read_bars("1h")
        finally:
            store.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def append_bars() -> int:
    df = _snapshot_bars()
    if df is None or df.empty:
        return 0
    df = df.reset_index() if "time" not in df.columns else df
    if "time" not in df.columns:
        return 0
    df["time"] = pd.to_datetime(df["time"], utc=True).astype(str)
    df = df.sort_values("time").tail(6)
    new = not BARS.exists()
    seen: set[str] = set()
    if not new:
        try:
            seen = {line.split(",")[0] for line in
                    BARS.read_text().splitlines()[1:]}
        except Exception:  # noqa: BLE001
            seen = set()
    written = 0
    with BARS.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(BAR_COLS)
        for _, r in df.iterrows():
            key = str(r["time"])
            if key in seen:
                continue
            w.writerow([key] + [r.get(c, "") for c in BAR_COLS[1:]])
            written += 1
    return written


def append_heartbeat() -> None:
    hb = ROOT / "data" / "heartbeat.json"
    try:
        d = json.loads(hb.read_text())
    except Exception:  # noqa: BLE001
        return
    new = not HB.exists()
    with HB.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["ts", "iso", "mode", "requested", "active", "equity",
                        "price", "session", "standby", "version"])
        w.writerow([d.get("ts"), d.get("iso"), d.get("mode"),
                    d.get("requested_broker"), d.get("active_broker"),
                    d.get("equity"), d.get("price"), d.get("session"),
                    d.get("standby"), d.get("version")])


def mirror_audit() -> None:
    src = ROOT / "data" / "audit.jsonl"
    try:
        if src.exists() and src.stat().st_size < 50_000_000:
            shutil.copyfile(src, AUDIT_MIRROR)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[capture] live validation sidecar -> {OUT}", flush=True)
    while True:
        try:
            n = append_bars()
            if n:
                print(f"[capture] +{n} bars", flush=True)
            append_heartbeat()
            mirror_audit()
        except Exception as e:  # noqa: BLE001 - sidecar never kills the run
            print(f"[capture] error: {e}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
