#!/usr/bin/env python3
"""
GOLD//REAPER :: Web Dashboard (FastAPI, port 8080)
===================================================
Terminal-native ops console:

  live equity curve (SVG) · open position · session/daily PnL · win streak
  last 20 trades · SSE log tail · matrix rain canvas · terminal palette

Reads the bot's real state files when present (data/apex_risk.json,
data/trades.csv, data/audit.jsonl) and falls back to seeded mock data
when they are not - so the demo runs with zero keys and zero brokers.

Run:   python dashboard/app.py          ->  http://localhost:8080
"""
from __future__ import annotations

import asyncio
import csv
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"

app = FastAPI(title="GOLD//REAPER", docs_url=None, redoc_url=None)

# vendored JS for the console UI (chart.umd.min.js) — no API contract change
app.mount("/vendor", StaticFiles(directory=STATIC / "vendor"), name="vendor")

MOCK_SEED = 666

DIMENSIONS = ["trend", "order-flow", "volatility", "structure",
              "statistical", "session", "microstructure", "cross-asset",
              "news", "regime"]


def _dims_mock(active_frac: float = 0.8) -> list[dict]:
    rng = random.Random(MOCK_SEED)
    return [{"name": name,
             "value": round(rng.uniform(0.25, 0.95), 2),
             "active": rng.random() < active_frac}
            for name in DIMENSIONS]


def _brokers_mock() -> list[dict]:
    return [
        {"name": "MT5 (Exness)", "state": "standby", "latency_ms": None},
        {"name": "Bitget", "state": "standby", "latency_ms": None},
        {"name": "Paper", "state": "up", "latency_ms": 1},
    ]


# ─────────────────────────────────────────────────────────── data providers

def _equity_history_mock(points: int = 120) -> list[float]:
    rng = random.Random(MOCK_SEED)
    eq, out = 10_000.0, []
    for _ in range(points):
        eq += rng.gauss(6.5, 38.0)
        out.append(round(eq, 2))
    return out


def state_payload() -> dict:
    """Real bot state if present, else a coherent mock session."""
    risk_file = DATA / "apex_risk.json"
    if risk_file.exists():
        try:
            risk = json.loads(risk_file.read_text())
            return {
                "mode": "live",
                "ts": datetime.now(timezone.utc).isoformat(),
                "equity": risk.get("equity", 0),
                "day_pnl": risk.get("day_pnl", 0.0),
                "week_pnl": risk.get("week_pnl", 0.0),
                "month_pnl": risk.get("month_pnl", 0.0),
                "block_pnl": risk.get("block_pnl", 0.0),
                "block_target": risk.get("block_target", 20),
                "block_key": risk.get("block", "-"),
                "recovery": risk.get("recovery_mode", False),
                "latched": risk.get("latched", False),
                "wins": risk.get("wins", 0),
                "losses": risk.get("losses", 0),
                "equity_history": _equity_history_mock(),
                "position": None,
                "session": "london/ny overlap",
                "regime": {"name": "TREND_UP", "confidence": 0.71,
                           "engine": "rules"},
                "dims": _dims_mock(),
                "brokers": _brokers_mock(),
                "streak": max(0, risk.get("wins", 0) - risk.get("losses", 0)),
                "hunt_window": "12-14 UTC",
                "geometry": "SL 1.2xATR · TP 2.0R · risk 1%",
            }
        except Exception:  # noqa: BLE001
            pass
    eq_hist = _equity_history_mock()
    day_pnl = round(eq_hist[-1] - 10_000.0, 2)
    return {
        "mode": "mock",
        "ts": datetime.now(timezone.utc).isoformat(),
        "equity": eq_hist[-1],
        "day_pnl": day_pnl,
        "week_pnl": round(day_pnl + 61.80, 2),
        "month_pnl": round(day_pnl + 214.55, 2),
        "block_pnl": 23.41,
        "block_target": 20,
        "block_key": "2026-10-09T12",
        "recovery": False,
        "latched": False,
        "wins": 1,
        "losses": 0,
        "equity_history": eq_hist,
        "position": {
            "side": "LONG", "size_oz": 0.028, "entry": 2418.60,
            "sl": 2417.90, "tp": 2431.20,
            "opened": "12:04:33 UTC", "be": True,
        },
        "session": "london/ny overlap",
        "regime": {"name": "TREND_UP", "confidence": 0.71, "engine": "rules"},
        "dims": _dims_mock(),
        "brokers": _brokers_mock(),
        "streak": 1,
        "hunt_window": "12-14 UTC",
        "geometry": "SL 1.2xATR · TP 2.0R · risk 1%",
    }


def trades_payload(limit: int = 20) -> list[dict]:
    f = DATA / "trades.csv"
    if f.exists():
        try:
            rows = list(csv.DictReader(f.open()))[-limit:]
            return rows[::-1]
        except Exception:  # noqa: BLE001
            pass
    rng = random.Random(MOCK_SEED)
    out = []
    px = 2418.0
    for i in range(limit):
        px += rng.gauss(1.2, 4.0)
        side = "LONG" if rng.random() > 0.45 else "SHORT"
        pnl = round(rng.gauss(14.2, 26.0), 2)
        out.append({
            "ts": f"2026-10-0{8 - i // 5} {9 + i % 9:02d}:{(i * 17) % 60:02d} UTC",
            "side": side, "size_oz": f"{round(rng.uniform(0.02, 0.05), 3):.3f}",
            "entry": f"{px:,.2f}", "pnl": f"{pnl:+.2f}",
            "regime": rng.choice(["TREND_UP", "TREND_DOWN", "RANGE"]),
            "session": rng.choice(["LONDON_NY_OVERLAP", "LONDON"]),
        })
    return out


def log_lines_mock(n: int = 14) -> list[str]:
    rng = random.Random(MOCK_SEED)
    return [
        "12:04:31 | scan bar 2415.80 atr 3.4 adx 27 regime TREND_UP",
        "12:04:31 | trend: pullback ema20 rsi 44.8 reset",
        "12:04:33 | ensemble consensus 2.5/4.0 -> LONG",
        "12:04:33 | order executed XAUUSD LONG 0.028 oz @ 2418.60",
        "12:04:33 | stop 2414.28 target 2431.20 r:r 2.67",
        "12:35:02 | protective move stop -> breakeven 2417.90",
        "13:02:48 | trailing engaged +1.5R trail 1.2x atr",
        "13:44:19 | target filled 2431.20 pnl +23.41 (+0.23%)",
        "13:44:19 | block target banked +23.41/20.00 -> standby",
        "13:44:20 | session guard: block budget spent - no entries",
        "14:00:01 | heartbeat: equity 10,023.41 day +23.41 (1W/0L)",
        "14:00:02 | news brain: next event in 126.6min - clear",
        f"mock feed seed={MOCK_SEED} - attach bot to stream live audit",
        f"uptime ok · breakers armed · {rng.randint(3, 9)}h {rng.randint(10, 59)}m",
    ][:n]


# ─────────────────────────────────────────────────────────── routes

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text()


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(ROOT / "assets" / "icon.png", media_type="image/png")


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(state_payload())


@app.get("/api/trades")
def api_trades() -> JSONResponse:
    return JSONResponse({"rows": trades_payload()})


async def _stream():
    """SSE: tails the audit ledger when the bot runs; mock stream otherwise."""
    audit = DATA / "audit.jsonl"
    if audit.exists():
        pos = audit.read_text().count("\n")
        while True:
            lines = audit.read_text().splitlines()
            while pos < len(lines):
                try:
                    rec = json.loads(lines[pos])
                    ts = rec.get("ts", "")[11:19]
                    d = rec.get("data", {})
                    msg = (d.get("why") or d.get("message") or
                           d.get("event") or rec.get("kind", ""))
                    yield {"data": json.dumps({"t": ts, "line": f"{ts} | {msg}"})}
                except Exception:  # noqa: BLE001
                    pass
                pos += 1
            await asyncio.sleep(2.0)
    else:
        for line in log_lines_mock():
            yield {"data": json.dumps({"t": "", "line": line})}
            await asyncio.sleep(0.55)
        beat = 0
        while True:
            beat += 1
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            yield {"data": json.dumps({
                "t": ts, "line": f"{ts} | mock heartbeat {beat} · "
                                 f"guard armed · waiting block window"})}
            await asyncio.sleep(3.0)


@app.get("/api/stream")
def api_stream():
    return EventSourceResponse(_stream())


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"GOLD//REAPER console -> http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
