#!/usr/bin/env python3
"""
GOLD//REAPER :: Web Dashboard (FastAPI, port 8080)
===================================================
Terminal-native ops console:

  live equity curve · open position · session/daily PnL · win streak
  trade analytics (PF · expectancy · drawdown · pnl histogram)
  hunt-window clock · last 20 trades · SSE log tail · matrix rain canvas

Reads the bot's real state files when present (data/apex_risk.json,
data/trades.csv, data/audit.jsonl) and falls back to seeded mock data
when they are not - so the demo runs with zero keys and zero brokers.
In live mode an empty journal renders an honest empty state, never mock
rows (the brand contract: losses published, fabrications never).

Endpoints: /api/state · /api/trades · /api/metrics · /api/track_record
           /api/news · /api/hpe (HPE shadow telemetry)
           GET+POST /api/standby (kill-switch) · /api/stream (SSE)

Run:   python dashboard/app.py          ->  http://localhost:8080
"""
from __future__ import annotations

import asyncio
import base64
import csv
import json
import os
import random
import secrets
import sys
import time
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
HEARTBEAT_FILE = DATA / "heartbeat.json"
STANDBY_FILE = DATA / "standby.flag"
NEWS_FILE = DATA / "news" / "calendar_latest.json"
HPE_FILE = DATA / "hpe_state.json"  # HPE shadow-mode telemetry (bot-written)
HEARTBEAT_FRESH_S = 180  # mirrors watchdog staleness threshold


def _console_version() -> str:
    """Console version = the pyproject version (single source of truth)."""
    try:
        for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                return "v" + line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


CONSOLE_VERSION = _console_version()

app = FastAPI(title="GOLD//REAPER", docs_url=None, redoc_url=None)

# vendored JS for the console UI (chart.umd.min.js) — no API contract change
app.mount("/vendor", StaticFiles(directory=STATIC / "vendor"), name="vendor")


# ────────────────────────────── P1-D: optional HTTP basic auth (middleware)

class _BasicAuth:
    """Middleware gate: when DASHBOARD_USER/DASHBOARD_PASS are set, every
    route (kill-switch included) demands HTTP basic auth. Timing-safe
    compare. Never logs the credentials."""

    def __init__(self, app) -> None:
        self.app = app
        self.user = os.getenv("DASHBOARD_USER", "").strip()
        self.pwd = os.getenv("DASHBOARD_PASS", "").strip()
        self.enabled = bool(self.user and self.pwd)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw = headers.get(b"authorization", b"")
        ok = False
        if raw.startswith(b"Basic "):
            try:
                decoded = base64.b64decode(raw[6:]).decode("utf-8", "replace")
                user, _, pwd = decoded.partition(":")
                ok = secrets.compare_digest(user, self.user) and \
                    secrets.compare_digest(pwd, self.pwd)
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", b'Basic realm="GOLD//REAPER"'),
                                (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})


app.add_middleware(_BasicAuth)


DEMO_BANNER_HTML = (
    '<div id="demo-mode-banner" role="alert" style="'
    'background:#3d0a0f;color:#ff5470;border:2px solid #ff1744;'
    'border-radius:10px;padding:14px 18px;margin:0 0 16px;'
    'font-size:15px;font-weight:700;letter-spacing:1px;'
    'text-align:center;box-shadow:0 0 24px rgba(255,23,68,.35);">'
    '&#9888; DEMO MODE &#8212; NOT REAL DATA. '
    '<span style="font-weight:400;color:#e6a3a8;font-size:13px;">'
    'This console is showing a seeded mock session. Start the bot '
    '(python bot.py) for live state - mock and real data must never '
    'look identical.</span></div>'
)

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


def _brokers_live(heartbeat: dict | None) -> list[dict]:
    """Honest broker rows for live mode: never invent latency.
    Paper reflects real bot liveness; the others report what we know
    (standby = configured-but-not-primary / not connected here).
    Carries the requested-vs-active distinction (P1-B): a paper row
    labels itself 'fallback from X' when the user asked for X."""
    fresh = bool(heartbeat and heartbeat.get("fresh"))
    mode = (heartbeat or {}).get("mode") or "PAPER"
    requested = str((heartbeat or {}).get("requested_broker", mode))
    active = str((heartbeat or {}).get("active_broker", mode))
    paper_state = "up" if fresh else "down"
    if active != requested:
        label = f"Paper (fallback from {requested})"
    else:
        label = f"Paper ({mode})" if fresh else "Paper"
    return [
        {"name": f"MT5 (Exness) · requested={requested}",
         "state": "up" if active == "MT5" else "standby",
         "latency_ms": None},
        {"name": f"Bitget · requested={requested}",
         "state": "up" if active == "BITGET" else "standby",
         "latency_ms": None},
        {"name": label, "state": paper_state, "latency_ms": None},
    ]


def _heartbeat_telemetry() -> dict | None:
    """The bot's real liveness telemetry (heartbeat.json), or None.
    fresh = written within HEARTBEAT_FRESH_S - the console renders an
    awaiting state, never fabricated regime/broker data, when stale."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        hb = json.loads(HEARTBEAT_FILE.read_text())
        ts = float(hb.get("ts", 0))
        age = max(0, int(time.time() - ts))
        reg = hb.get("regime") if isinstance(hb.get("regime"), dict) else None
        conf = reg.get("probability") if reg else None
        return {
            "age_s": age,
            "fresh": age <= HEARTBEAT_FRESH_S,
            "iso": str(hb.get("iso", "")),
            "version": str(hb.get("version", "")),
            "mode": str(hb.get("mode", "")),
            "price": hb.get("price"),
            "session": hb.get("session"),
            "requested_broker": hb.get("requested_broker"),
            "active_broker": hb.get("active_broker"),
            "broker_label": hb.get("broker_label"),
            "regime": ({"name": reg.get("regime"),
                        "confidence": conf, "engine": reg.get("engine")}
                       if reg else None),
            "position": (hb.get("position")
                         if isinstance(hb.get("position"), dict) else None),
            "standby": bool(hb.get("standby")),
        }
    except Exception:  # noqa: BLE001
        return None


def standby_state() -> bool:
    """Kill-switch state: the flag file's existence IS the state."""
    return STANDBY_FILE.exists()


def set_standby(on: bool) -> bool:
    try:
        if on:
            STANDBY_FILE.parent.mkdir(parents=True, exist_ok=True)
            STANDBY_FILE.write_text(json.dumps({
                "on": True, "ts": time.time(), "source": "dashboard"}),
                encoding="utf-8")
        else:
            STANDBY_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return standby_state()


def news_payload(limit: int = 8) -> dict:
    """Upcoming calendar events from the bot's cached ForexFactory pull.
    Informational only - the bot enforces its own news blackout."""
    if not NEWS_FILE.exists():
        return {"events": [], "fetched_at": None,
                "source": "no calendar cached - bot fetches every 30 min"}
    try:
        raw = json.loads(NEWS_FILE.read_text())
        now = datetime.now(timezone.utc)
        out: list[dict] = []
        for ev in raw.get("events", []):
            try:
                when = datetime.fromisoformat(str(ev.get("date", "")))
            except ValueError:
                continue
            mins = round((when - now).total_seconds() / 60, 1)
            if mins < -720:  # skip day-old past events
                continue
            out.append({"title": str(ev.get("title", "?")),
                        "country": str(ev.get("country", "")),
                        "impact": str(ev.get("impact", "")),
                        "iso": when.isoformat(),
                        "min_until": mins})
        out.sort(key=lambda e: e["min_until"])
        return {"events": out[:limit],
                "fetched_at": str(raw.get("fetched_at", "")),
                "source": "data/news/calendar_latest.json"}
    except Exception:  # noqa: BLE001
        return {"events": [], "fetched_at": None,
                "source": "calendar cache unreadable"}


def _hpe_mock() -> dict:
    """Seeded HPE shadow demo - deterministic, no randomness at request time.
    Honest-looking disarmed session: 4 of 8 modules lean LONG (ml's vote is
    discounted below the 0.35 strength floor), cross_asset leans SHORT, the
    rest abstain - so alignment lands 3/4 and the decision is rejected.
    Histogram skews to the 0.4-0.6 band; zero orders ever placed."""
    return {
        "updated_at": "2026-10-09T12:04:33+00:00",
        "mode": "SHADOW",
        "strategy": "HPE",
        "last_decision": {
            "side": "LONG",
            "confidence": 0.58,
            "aligned": 3,
            "required": 4,
            "votes": {
                "trend": {"dir": 1, "strength": 0.72,
                          "reason": "ema20>ema50 · pullback rsi 44.8"},
                "meanrev": {"dir": 1, "strength": 0.55,
                            "reason": "stretch +1.8z fading into ema20"},
                "breakout": {"dir": 1, "strength": 0.48,
                             "reason": "2412 base retest · adx 27"},
                "psychology": {"dir": 0, "strength": 0.0,
                               "reason": "fg 62 GREED · no veto, no edge"},
                "micro": {"dir": 0, "strength": 0.0,
                          "reason": "doji close · flat anatomy"},
                "cross_asset": {"dir": -1, "strength": 0.30,
                                "reason": "dxy +0.4% · vix 14.1 soft"},
                "news": {"dir": 0, "strength": 0.0,
                         "reason": "nfp in 126m · outside blackout"},
                "ml": {"dir": 1, "strength": 0.33,
                       "reason": "lgbm p_win 0.51 · below 0.35 floor"},
            },
            "vetoes": ["agreement:3/4 — below required 4"],
            "regime": "TREND_UP",
            "session": "LONDON_NY_OVERLAP",
            "explain": [
                "trend: LONG (0.72) · ema20>ema50, pullback rsi 44.8",
                "meanrev: LONG (0.55) · stretch +1.8z fading",
                "breakout: LONG (0.48) · 2412 base retest",
                "psychology: abstain · fg 62 GREED, no veto",
                "micro: abstain · doji close, flat anatomy",
                "cross_asset: SHORT (0.30) · dxy +0.4%, vix soft",
                "news: abstain · nfp in 126m, outside blackout",
                "ml: LONG (0.33) discounted · below 0.35 strength floor",
                "verdict: REJECTED · agreement 3/4 · no order (shadow)",
            ],
            "accepted": False,
        },
        "last_signal": None,
        "psychology": {"fear_greed": 62.0, "regime_tag": "GREED",
                       "capitulation": 0, "euphoria": 0},
        "confidence_histogram": {"0.0-0.2": 6, "0.2-0.4": 21, "0.4-0.6": 49,
                                 "0.6-0.8": 8, "0.8-1.0": 0},
        "agreement_matrix": {
            "trend": {"LONG": 9, "SHORT": 4, "abstain": 71},
            "meanrev": {"LONG": 6, "SHORT": 5, "abstain": 73},
            "breakout": {"LONG": 3, "SHORT": 2, "abstain": 79},
            "psychology": {"LONG": 2, "SHORT": 1, "abstain": 81},
            "micro": {"LONG": 5, "SHORT": 3, "abstain": 76},
            "cross_asset": {"LONG": 1, "SHORT": 6, "abstain": 77},
            "news": {"LONG": 0, "SHORT": 1, "abstain": 83},
            "ml": {"LONG": 1, "SHORT": 0, "abstain": 83},
        },
        "shadow_stats": {"decisions": 84, "would_trade": 0, "vetoed": 5,
                         "alignment_max": 3},
        "source": "mock",
    }


def hpe_payload() -> dict:
    """HPE (high-probability engine) shadow-mode telemetry.

    The strategy is DISARMED: it logs votes, vetoes and confidence but
    places no orders. Live reads data/hpe_state.json (bot-written); a
    missing or corrupt file renders an honest idle state - live never
    falls back to the seeded mock (same brand contract as the journal)."""
    if not _is_live():
        return _hpe_mock()

    def _empty(note: str) -> dict:
        return {"updated_at": None, "mode": "SHADOW", "strategy": "HPE",
                "last_decision": None, "last_signal": None,
                "psychology": None, "confidence_histogram": {},
                "agreement_matrix": {}, "shadow_stats": None,
                "source": note}

    if not HPE_FILE.exists():
        return _empty("data/hpe_state.json — not written yet")
    try:
        raw = json.loads(HPE_FILE.read_text())
        if not isinstance(raw, dict):
            raise ValueError("hpe state must be a json object")
        raw.setdefault("strategy", "HPE")
        raw.setdefault("mode", "SHADOW")
        raw.setdefault("updated_at", None)
        raw.setdefault("last_decision", None)
        raw.setdefault("last_signal", None)
        raw.setdefault("psychology", None)
        raw.setdefault("confidence_histogram", {})
        raw.setdefault("agreement_matrix", {})
        raw.setdefault("shadow_stats", None)
        raw["source"] = "data/hpe_state.json"
        return raw
    except Exception:  # noqa: BLE001 - corrupt state renders idle, never mock
        return _empty("data/hpe_state.json — unreadable")


# ─────────────────────────────────────────────────────────── data providers

def _equity_history_mock(points: int = 120) -> list[float]:
    rng = random.Random(MOCK_SEED)
    eq, out = 10_000.0, []
    for _ in range(points):
        eq += rng.gauss(6.5, 38.0)
        out.append(round(eq, 2))
    return out


def _fills() -> list[dict]:
    """Realized fills from the audit ledger - the source of truth for PnL.
    (trades.csv journals ENTRIES with planned risk; realized pnl only ever
    lands in audit.jsonl 'fill' events, emitted by bot._manage_positions.)"""
    f = DATA / "audit.jsonl"
    out: list[dict] = []
    if not f.exists():
        return out
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("kind") != "fill":
                continue
            d = rec.get("data", {}) or {}
            try:
                pnl = float(d.get("pnl", 0))
            except (TypeError, ValueError):
                continue
            out.append({"ts": str(rec.get("ts", "")), "pnl": pnl,
                        "block_pnl": d.get("block_pnl")})
    except Exception:  # noqa: BLE001
        pass
    return out


def _entries() -> list[dict]:
    """Entry journal rows (planned risk at order time)."""
    f = DATA / "trades.csv"
    if not f.exists():
        return []
    try:
        return list(csv.DictReader(f.open(newline="", encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return []


def _start_balance() -> float | None:
    """Balance the paper account started from (reconstructed)."""
    fills = _fills()
    try:
        bal = json.loads((DATA / "apex_risk.json").read_text()).get("balance")
        if isinstance(bal, (int, float)) and bal > 0:
            return float(bal) - sum(f["pnl"] for f in fills)
    except Exception:  # noqa: BLE001
        pass
    return None


def _equity_history_from_fills(points: int = 120) -> list[float] | None:
    """Real stepwise equity curve from realized fills.
    Returns None when nothing has closed yet - live callers then render a
    flat line at the true equity (never the mock walk)."""
    fills = _fills()
    if not fills:
        return None
    base = _start_balance()
    if base is None:
        base = 10_000.0 - sum(f["pnl"] for f in fills)
    eq, walk = base, []
    for f in fills:
        eq += f["pnl"]
        walk.append(round(eq, 2))
    if len(walk) >= points:
        return [round(base, 2)] + walk[-points:]
    return [round(base, 2)] * (points - len(walk)) + walk


def _equity_marks(limit: int = 60) -> list[dict]:
    """Session-boundary equity snapshots the bot appended to
    data/equity_marks.csv (ts, equity, session). Honest real snapshots
    only - a missing or unreadable file simply yields []."""
    f = DATA / "equity_marks.csv"
    if not f.exists():
        return []
    try:
        rows = list(csv.DictReader(f.open(newline="", encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for r in rows:
        try:
            out.append({"ts": str(r.get("ts", "")),
                        "eq": float(r.get("equity", 0)),
                        "session": str(r.get("session", ""))})
        except (TypeError, ValueError):
            continue
    return out[-limit:]


def _equity_history_live(points: int = 120) \
        -> tuple[list[float] | None, list[str] | None, list[int] | None]:
    """Time-ordered live equity curve merged from two honest sources:
      - realized fills (audit.jsonl 'fill' events, pnl steps from base)
      - session-boundary marks (equity_marks.csv, absolute snapshots)
    Returns (values, times, mark_idx). times is a parallel HH:MM label
    array (None when no marks exist -> console keeps its index axis);
    mark_idx lists which indices are session marks (chart dot overlay)."""
    marks = _equity_marks()
    fills = _fills()
    if not marks:
        return (_equity_history_from_fills() if fills else None, None, None)
    events: list[tuple[str, float, bool]] = \
        [(m["ts"], m["eq"], True) for m in marks]
    base = _start_balance()
    if base is None:
        base = 10_000.0 - sum(f["pnl"] for f in fills)
    eq = base
    for f in fills:
        eq += f["pnl"]
        events.append((f["ts"], round(eq, 2), False))
    events.sort(key=lambda e: e[0])
    tail = events[-points:]
    vals = [round(e[1], 2) for e in tail]
    times: list[str] = []
    for e in tail:
        try:
            times.append(datetime.fromisoformat(e[0]).strftime("%H:%M"))
        except (TypeError, ValueError):
            times.append("")
    mark_idx = [i for i, e in enumerate(tail) if e[2]]
    return (vals, times, mark_idx)


def _flat_equity(value: float, points: int = 120) -> list[float]:
    """Honest flat curve for a live account with zero realized fills."""
    try:
        v = round(float(value), 2)
    except (TypeError, ValueError):
        v = 0.0
    return [v] * points


def state_payload() -> dict:
    """Real bot state if present, else a coherent mock session."""
    risk_file = DATA / "apex_risk.json"
    if risk_file.exists():
        try:
            risk = json.loads(risk_file.read_text())
            eq = risk.get("equity", 0)
            hist, eq_times, mark_idx = _equity_history_live()
            hb = _heartbeat_telemetry()
            fresh_regime = hb.get("regime") if (hb and hb["fresh"]) else None
            fresh_session = hb.get("session") if (hb and hb["fresh"]) else None
            return {
                "mode": "live",
                "ts": datetime.now(timezone.utc).isoformat(),
                "equity": eq,
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
                "equity_source": ("marks+fills" if eq_times
                                  else ("fills" if hist else "flat")),
                "equity_history": hist or _flat_equity(eq),
                "equity_times": eq_times,
                "equity_mark_idx": mark_idx,
                # open position from the bot's own heartbeat snapshot —
                # shown even when the heartbeat is stale (it is the last
                # real state the bot published; the bot chip flags staleness)
                "position": (hb.get("position") if hb else None),
                "heartbeat": hb,
                "standby": standby_state(),
                "price": hb.get("price") if hb else None,
                "session": fresh_session,
                "regime": fresh_regime,
                "dims": None,  # per-dimension values only exist inside the bot
                "brokers": _brokers_live(hb),
                "broker_requested": (hb or {}).get("requested_broker"),
                "broker_active": (hb or {}).get("active_broker"),
                "broker_label": (hb or {}).get("broker_label"),
                "streak": max(0, risk.get("wins", 0) - risk.get("losses", 0)),
                "hunt_window": "12-14 UTC",
                "geometry": "SL 1.2xATR · TP 2.0R · risk 1%",
                "version": CONSOLE_VERSION,
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
        "equity_times": None,
        "equity_mark_idx": None,
        "position": {
            "side": "LONG", "size_oz": 0.028, "entry": 2418.60,
            "sl": 2417.90, "tp": 2431.20,
            "opened": "12:04:33 UTC", "be": True,
        },
        "heartbeat": None,
        "standby": False,
        "price": None,
        "session": "london/ny overlap",
        "regime": {"name": "TREND_UP", "confidence": 0.71, "engine": "rules"},
        "dims": _dims_mock(),
        "brokers": _brokers_mock(),
        "streak": 1,
        "hunt_window": "12-14 UTC",
        "geometry": "SL 1.2xATR · TP 2.0R · risk 1%",
        "version": CONSOLE_VERSION,
    }


def track_record_payload() -> dict:
    """Parse the append-only paper ledger (docs/TRACK_RECORD.md)."""
    f = ROOT / "docs" / "TRACK_RECORD.md"
    rows: list[dict] = []
    if f.exists():
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.startswith("|") or "---" in line or "date (UTC)" in line:
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 3:
                    rows.append({"date": cells[0], "trades": cells[1],
                                 "net": cells[2],
                                 "equity": cells[3] if len(cells) > 3 else "-",
                                 "note": cells[5] if len(cells) > 5 else ""})
        except Exception:  # noqa: BLE001
            pass
    total = 0.0
    for r in rows:
        try:
            total += float(r["net"].replace("+", ""))
        except ValueError:
            continue
    return {"rows": rows[::-1][:10], "days": len(rows),
            "total_net": round(total, 2),
            "source": "docs/TRACK_RECORD.md (append-only)"}


def _mock_trades(limit: int = 20) -> list[dict]:
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
            "r": f"{pnl / 100.0:+.2f}",  # mock risk = 1% of the 10k account
            "regime": rng.choice(["TREND_UP", "TREND_DOWN", "RANGE"]),
            "session": rng.choice(["LONDON_NY_OVERLAP", "LONDON"]),
        })
    return out


def _is_live() -> bool:
    """The bot's real state file exists - never dress it up with mock data."""
    return (DATA / "apex_risk.json").exists()


def trades_payload(limit: int = 20) -> dict:
    """Live: entry journal enriched with realized pnl + R multiple
    (k-th fill closes the k-th entry - the bot holds one position at a
    time). R = realized pnl / planned risk, the honest unit of the
    locked geometry. Mock otherwise."""
    entries = _entries()
    fills = _fills()
    if entries or fills:
        rows: list[dict] = []
        for i, e in enumerate(entries):
            ts = str(e.get("ts_utc", ""))[:19].replace("T", " ")
            try:
                entry = f"{float(str(e.get('entry', '')).replace(',', '')):,.2f}"
            except (TypeError, ValueError):
                entry = str(e.get("entry", "—"))
            if i < len(fills):
                pnl = fills[i]["pnl"]
                try:
                    risk = float(str(e.get("risk_usd", "")).replace(",", ""))
                    r = f"{pnl / risk:+.2f}" if risk > 0 else "—"
                except (TypeError, ValueError):
                    r = "—"
                pnl_s = f"{pnl:+.2f}"
            else:
                pnl_s, r = "—", "—"
            rows.append({
                "ts": ts + " UTC" if ts else "—",
                "side": e.get("side", "—"),
                "size_oz": e.get("size_oz", "—"),
                "entry": entry,
                "pnl": pnl_s,
                "r": r,
                "regime": e.get("regime", "—"),
                "session": e.get("session", "—"),
            })
        # fills without a journaled entry must never be hidden
        for j in range(len(entries), len(fills)):
            f = fills[j]
            rows.append({"ts": f["ts"][:19].replace("T", " ") + " UTC",
                         "side": "—", "size_oz": "—", "entry": "—",
                         "pnl": f"{f['pnl']:+.2f}", "r": "—",
                         "regime": "—", "session": "—"})
        return {"mode": "live", "rows": rows[::-1][:limit]}
    if _is_live():
        # live account, zero activity yet - honest empty state, not mock rows
        return {"mode": "live", "rows": []}
    return {"mode": "mock", "rows": _mock_trades(limit)}


def _compute_metrics(pnls: list[float], metas: list[dict],
                     start: float) -> dict:
    """Quant stats over realized pnl: PF, expectancy, max DD, histogram."""
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    eq, peak, max_dd, max_dd_pct, underwater = start, start, 0.0, 0.0, []
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = eq - peak
        underwater.append(round(dd / peak * 100, 3) if peak else 0.0)
        if dd < max_dd:
            max_dd = dd
            max_dd_pct = round(dd / peak * 100, 2)
    lw = ll = cw = cl = 0
    for p in pnls:
        if p > 0:
            cw, cl = cw + 1, 0
        elif p < 0:
            cl, cw = cl + 1, 0
        else:
            cw = cl = 0
        lw, ll = max(lw, cw), max(ll, cl)
    by_regime: dict[str, dict] = {}
    for p, m in zip(pnls, metas):
        slot = by_regime.setdefault(str(m.get("regime") or "—"),
                                    {"n": 0, "net": 0.0})
        slot["n"] += 1
        slot["net"] = round(slot["net"] + p, 2)
    hist: dict = {"labels": [], "counts": [], "pos": []}
    if n:
        lo, hi = min(pnls), max(pnls)
        if hi <= lo:
            hi = lo + 1.0
        k = 8
        width = (hi - lo) / k
        counts = [0] * k
        for p in pnls:
            counts[min(k - 1, max(0, int((p - lo) / width)))] += 1
        for i in range(k):
            a, b = lo + width * i, lo + width * (i + 1)
            hist["labels"].append(f"{a:+.0f}..{b:+.0f}")
            hist["counts"].append(counts[i])
            hist["pos"].append((a + b) / 2 >= 0)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "pf": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / n, 2) if n else 0.0,
        "net": round(sum(pnls), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else None,
        "best": round(max(pnls), 2) if n else None,
        "worst": round(min(pnls), 2) if n else None,
        "max_drawdown": round(max_dd, 2), "max_drawdown_pct": max_dd_pct,
        "longest_win_streak": lw, "longest_loss_streak": ll,
        "histogram": hist, "underwater": underwater,
        "by_regime": by_regime,
        "start_balance": round(start, 2),
    }


def metrics_payload() -> dict:
    """Analytics over REALIZED fills (live) or the seeded mock session."""
    fills = _fills()
    if fills:
        entries = _entries()
        metas = [{"regime": (entries[i].get("regime") if i < len(entries) else None),
                  "session": (entries[i].get("session") if i < len(entries) else None)}
                 for i in range(len(fills))]
        start = _start_balance() or 10_000.0
        m = _compute_metrics([f["pnl"] for f in fills], metas, start)
        m["source"] = "live"
        return m
    if _is_live():
        m = _compute_metrics([], [], _start_balance() or 1000.0)
        m["source"] = "live"
        return m
    rows = _mock_trades(20)
    pnls = [float(r["pnl"]) for r in rows]
    metas = [{"regime": r["regime"], "session": r["session"]} for r in rows]
    m = _compute_metrics(pnls, metas, 10_000.0)
    m["source"] = "mock"
    return m


def _fmt_audit(rec: dict) -> str:
    """Human one-liner for an audit record (SSE log tail)."""
    kind = rec.get("kind", "?")
    d = rec.get("data", {}) or {}
    if kind == "fill":
        try:
            pnl = float(d.get("pnl", 0))
        except (TypeError, ValueError):
            pnl = 0.0
        try:
            blk = float(d.get("block_pnl", 0))
        except (TypeError, ValueError):
            blk = 0.0
        return f"fill {pnl:+.2f} USD · block {blk:+.2f}"
    if kind == "lifecycle":
        ev = str(d.get("event", ""))
        if ev == "start":
            return f"reaper online · broker {d.get('broker', '?')}"
        if ev == "stop":
            return "reaper offline · clean shutdown"
        if ev == "breakeven":
            return (f"protective stop -> breakeven "
                    f"ticket {d.get('ticket', '?')} @ {d.get('sl', '?')}")
        return f"lifecycle · {ev or kind}"
    if kind == "skip":
        reg = d.get("regime", {}) or {}
        news = d.get("news", {}) or {}
        try:
            prob = int(float(reg.get("probability") or 0) * 100)
        except (TypeError, ValueError):
            prob = 0
        bits = [f"skip · {reg.get('regime', '?')} {prob}%"]
        try:
            bits.append(f"adx {float(reg.get('adx') or 0):.0f}")
        except (TypeError, ValueError):
            pass
        if news.get("next_event"):
            bits.append(f"news: {news['next_event']} "
                        f"in {news.get('min_to_event', '?')}m")
        votes = d.get("votes") or {}
        if votes:
            bits.append("votes " + " ".join(
                f"{k}:{v}" for k, v in list(votes.items())[:3]))
        else:
            bits.append("no confluence")
        return " · ".join(str(b) for b in bits)
    if kind == "order":
        if d.get("ok"):
            return "order executed"
        return f"order rejected · {d.get('error', 'unknown')}"
    return f"{kind} · {json.dumps(d)[:120]}"


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

@app.get("/health")
def health() -> JSONResponse:
    """Liveness probe for installer/health_check.py, watchdogs and docs."""
    return JSONResponse({"status": "ok", "bot": "GOLD-REAPER",
                         "version": CONSOLE_VERSION,
                         "mode": "live" if _is_live() else "mock"})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Console shell. P1-D: when mock mode is active the served HTML
    carries a large red DEMO banner injected server-side - mock and
    real data must never look identical."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if not _is_live():
        marker = "<body>"
        if marker in html:
            html = html.replace(marker, marker + "\n" + DEMO_BANNER_HTML, 1)
    return html


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(ROOT / "assets" / "icon.png", media_type="image/png")


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(state_payload())


@app.get("/api/trades")
def api_trades() -> JSONResponse:
    return JSONResponse(trades_payload())


@app.get("/api/metrics")
def api_metrics() -> JSONResponse:
    return JSONResponse(metrics_payload())


@app.get("/api/track_record")
def api_track_record() -> JSONResponse:
    return JSONResponse(track_record_payload())


@app.get("/api/news")
def api_news() -> JSONResponse:
    return JSONResponse(news_payload())


@app.get("/api/hpe")
def api_hpe() -> JSONResponse:
    """HPE shadow telemetry: votes, vetoes, psychology, decision stats."""
    return JSONResponse(hpe_payload())


@app.get("/api/standby")
def api_standby_get() -> JSONResponse:
    return JSONResponse({"on": standby_state(),
                         "note": "flag file gates NEW entries only; "
                                 "open positions keep being managed"})


@app.post("/api/standby")
def api_standby_post(body: dict) -> JSONResponse:
    on = bool(body.get("on"))
    return JSONResponse({"on": set_standby(on)})


def _state_event() -> dict:
    """Named SSE event carrying the full state payload.

    The console consumes these as the primary state source and keeps
    its poll only as an offline fallback (poll widens 5s -> 15s once a
    push has been seen). Named events leave the existing default
    `message` log-tail channel untouched."""
    return {"event": "state", "data": json.dumps(state_payload())}


async def _stream():
    """SSE: seeds recent audit history, then tails new records live;
    mock stream when no audit ledger exists. Both branches also push
    the full state payload as named `state` events (~6s cadence)."""
    audit = DATA / "audit.jsonl"
    if audit.exists():

        def _events() -> list[dict]:
            out = []
            for line in audit.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                ts = str(rec.get("ts", ""))[11:19]
                out.append({"t": ts,
                            "line": f"{ts} | {_fmt_audit(rec)}"})
            return out

        # seed the panel with recent history (chronological - the UI
        # prepends, so the newest record ends up on top)
        for ev in _events()[-14:]:
            yield {"data": json.dumps(ev)}
        pos = audit.read_text().count("\n")
        beat = 0
        while True:
            lines = audit.read_text().splitlines()
            while pos < len(lines):
                try:
                    rec = json.loads(lines[pos])
                    ts = str(rec.get("ts", ""))[11:19]
                    yield {"data": json.dumps(
                        {"t": ts, "line": f"{ts} | {_fmt_audit(rec)}"})}
                except Exception:  # noqa: BLE001
                    pass
                pos += 1
            beat += 1
            if beat % 3 == 0:  # ~every 6s: push state over SSE
                yield _state_event()
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
            if beat % 3 == 0:  # ~every 9s: push state over SSE
                yield _state_event()
            await asyncio.sleep(3.0)


@app.get("/api/stream")
def api_stream():
    return EventSourceResponse(_stream())


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    try:
        from core.config_validation import resolve_dashboard_bind
        host, port = resolve_dashboard_bind(port)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - bind misconfig must be loud
        print(f"[DASHBOARD] refusing to start: {e}")
        return 2
    auth = "on" if (os.getenv("DASHBOARD_USER") and
                    os.getenv("DASHBOARD_PASS")) else "off"
    print(f"GOLD//REAPER console -> http://{host}:{port} "
          f"(bind {host}, basic-auth {auth})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
