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
           /api/news · GET+POST /api/standby (kill-switch) · /api/stream (SSE)

Run:   python dashboard/app.py          ->  http://localhost:8080
"""
from __future__ import annotations

import asyncio
import csv
import json
import random
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
HEARTBEAT_FRESH_S = 180  # mirrors watchdog staleness threshold

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


def _brokers_live(heartbeat: dict | None) -> list[dict]:
    """Honest broker rows for live mode: never invent latency.
    Paper reflects real bot liveness; the others report what we know
    (standby = configured-but-not-primary / not connected here)."""
    fresh = bool(heartbeat and heartbeat.get("fresh"))
    mode = (heartbeat or {}).get("mode") or "PAPER"
    paper_state = "up" if fresh else "down"
    return [
        {"name": "MT5 (Exness)", "state": "standby", "latency_ms": None},
        {"name": "Bitget", "state": "standby", "latency_ms": None},
        {"name": f"Paper ({mode})" if fresh else "Paper",
         "state": paper_state, "latency_ms": None},
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
            hist = _equity_history_from_fills()
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
                "equity_source": "fills" if hist else "flat",
                "equity_history": hist or _flat_equity(eq),
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


@app.get("/api/standby")
def api_standby_get() -> JSONResponse:
    return JSONResponse({"on": standby_state(),
                         "note": "flag file gates NEW entries only; "
                                 "open positions keep being managed"})


@app.post("/api/standby")
def api_standby_post(body: dict) -> JSONResponse:
    on = bool(body.get("on"))
    return JSONResponse({"on": set_standby(on)})


async def _stream():
    """SSE: seeds recent audit history, then tails new records live;
    mock stream when no audit ledger exists."""
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
