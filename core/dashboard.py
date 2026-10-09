"""
GOLD REAPER APEX :: Live Dashboard (FastAPI + auto-refresh HTMX-style)
=======================================================================
Dark ops console served at http://localhost:8050 — shows the whole hunt:

  regime · news state · 4h-block PnL vs $20 target · day/week/month PnL
  breaker status · open positions · last 100 journal rows · audit tail
  equity sparkline · reaper vitals

Run:  python core/dashboard.py          (or the bot spawns it with --dashboard)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:  # pragma: no cover
    raise SystemExit("pip install fastapi uvicorn")

import uvicorn  # noqa: E402

app = FastAPI(title="GOLD REAPER APEX", docs_url=None, redoc_url=None)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def _csv_tail(path: Path, n: int = 100) -> list[list[str]]:
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    head = lines[0].split(",") if lines else []
    rows = [ln.split(",") for ln in lines[1:][-n:]]
    return [head] + rows if head else rows


def _audit_tail(n: int = 40) -> list[dict]:
    from core.audit import tail
    return tail(n)


def _equity_series() -> list[float]:
    rows = _csv_tail(DATA / "trades.csv", 400)
    if len(rows) < 2:
        return []
    try:
        # try pnl column in risk ledger first
        led = _read_json(DATA / "apex_risk.json", {})
        return [led.get("equity", 0)]
    except Exception:  # noqa: BLE001
        return []


STATE_FILE = DATA / "apex_risk.json"
HEARTBEAT_FILE = DATA / "state.json"


@app.get("/health")
def health():
    return {"status": "ok", "bot": "GOLD-REAPER-APEX"}


@app.get("/api/state")
def api_state():
    return JSONResponse({
        "risk": _read_json(STATE_FILE, {}),
        "core_state": _read_json(HEARTBEAT_FILE, {}),
        "breakers": _read_json(DATA / "breakers.json", {"latched": False}),
    })


@app.get("/api/trades")
def api_trades():
    rows = _csv_tail(DATA / "trades.csv", 100)
    head = rows[0] if rows else []
    return JSONResponse({"columns": head, "rows": rows[1:]})


@app.get("/api/audit")
def api_audit():
    return JSONResponse({"events": _audit_tail(40)})


PAGE = """<!doctype html>
<html><head><title>GOLD REAPER APEX</title>
<meta charset="utf-8"><meta http-equiv="refresh" content="5">
<style>
  :root { color-scheme: dark; }
  body { background:#050608; color:#c9d1d9; font-family:'Cascadia Code',Consolas,monospace;
         margin:0; padding:24px; }
  h1 { color:#ff1744; letter-spacing:4px; margin:0 0 4px; }
  .sub { color:#8b949e; margin-bottom:22px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
          gap:14px; margin-bottom:22px; }
  .card { background:#0d1117; border:1px solid #21262d; border-radius:10px;
          padding:14px 16px; }
  .card h3 { margin:0 0 8px; font-size:12px; color:#8b949e; letter-spacing:2px; }
  .big { font-size:26px; font-weight:700; }
  .green { color:#3fb950; } .red { color:#ff1744; } .amber { color:#d29922; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th,td { text-align:left; padding:4px 8px; border-bottom:1px solid #21262d; }
  th { color:#8b949e; }
  .badge { display:inline-block; padding:2px 10px; border-radius:20px; font-size:11px;
           border:1px solid #30363d; }
  .latched { background:#3d0a0f; color:#ff1744; border-color:#ff1744; }
  .ok { background:#0b3d1f; color:#3fb950; }
</style></head><body>
<h1>☠️ GOLD REAPER APEX</h1>
<div class="sub">autonomous XAU/USD hunter · refresh 5s · UTC {now}</div>
<div class="grid">
  <div class="card"><h3>EQUITY</h3><div class="big">{equity}</div></div>
  <div class="card"><h3>4H BLOCK PNL / TARGET</h3>
    <div class="big {block_cls}">{block_pnl} / {block_target}</div>
    <div class="sub">{block_key} · attempts {attempts}</div></div>
  <div class="card"><h3>DAY PNL</h3><div class="big {day_cls}">{day_pnl}</div>
    <div class="sub">week {week_pnl} · month {month_pnl}</div></div>
  <div class="card"><h3>BREAKERS</h3>
    <div class="badge {brk_cls}">{brk}</div>
    <div class="sub">risk now {risk_pct}% · recovery {recovery}</div></div>
  <div class="card"><h3>REGIME</h3><div class="big">{regime}</div>
    <div class="sub">{regime_engine}</div></div>
  <div class="card"><h3>NEWS DIMENSION</h3><div class="big">{sentiment}</div>
    <div class="sub">{news_note}</div></div>
</div>
<h3 style="color:#8b949e">RECENT AUDIT TRAIL</h3>
<table><tr><th>time</th><th>kind</th><th>summary</th></tr>
{audit_rows}
</table>
<h3 style="color:#8b949e">TRADE JOURNAL (last 100)</h3>
<table><tr>{trade_head}</tr>{trade_rows}</table>
</body></html>"""


def _fmt_pair(x: float) -> str:
    return f"{x:+.2f}" if isinstance(x, (int, float)) else str(x)


@app.get("/", response_class=HTMLResponse)
def index():
    from datetime import datetime, timezone
    risk = _read_json(STATE_FILE, {})
    brk = _read_json(DATA / "breakers.json", {"latched": False})
    audits = _audit_tail(12)
    rows = _csv_tail(DATA / "trades.csv", 100)

    equity = risk.get("equity", "-")
    block_pnl = risk.get("block_pnl", 0)
    day_pnl = risk.get("day_pnl", 0)
    regime = (audits[-1]["data"].get("regime", {}) if audits and
              isinstance(audits[-1]["data"], dict) else {}) or {}
    news = (audits[-1]["data"].get("news", {}) if audits and
            isinstance(audits[-1]["data"], dict) else {}) or {}

    audit_html = ""
    for a in reversed(audits):
        d = a.get("data", {})
        summary = d.get("why") or d.get("message") or d.get("event") or ""
        audit_html += (f"<tr><td>{str(a.get('ts',''))[:19]}</td>"
                       f"<td>{a.get('kind','')}</td>"
                       f"<td>{str(summary)[:90]}</td></tr>")

    head_html = "".join(f"<th>{h}</th>" for h in (rows[0] if rows else []))
    body_html = ""
    for r in reversed(rows[1:]):
        body_html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    return PAGE.format(
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        equity=equity,
        block_pnl=_fmt_pair(block_pnl),
        block_cls="green" if isinstance(block_pnl, (int, float)) and block_pnl >= 0 else "red",
        block_target=risk.get("block_target", 20),
        block_key=risk.get("block", "-"),
        attempts=risk.get("block_attempts", 0),
        day_pnl=_fmt_pair(day_pnl),
        day_cls="green" if isinstance(day_pnl, (int, float)) and day_pnl >= 0 else "red",
        week_pnl=_fmt_pair(risk.get("week_pnl", 0)),
        month_pnl=_fmt_pair(risk.get("month_pnl", 0)),
        brk="LATCHED" if brk.get("latched") else "ARMED",
        brk_cls="latched" if brk.get("latched") else "ok",
        risk_pct=risk.get("risk_fraction_now", "-"),
        recovery="ON" if risk.get("recovery_mode") else "off",
        regime=regime.get("regime", "—"),
        regime_engine=f"engine {regime.get('engine', '-')} · p={regime.get('probability', '-')}",
        sentiment=f"{news.get('sentiment', 0):+.2f}" if news else "—",
        news_note=(f"next: {news.get('next_event', '')[:40]} "
                   f"in {news.get('min_to_event', '-')}min") if news else "no data",
        audit_rows=audit_html or "<tr><td colspan=3>silent</td></tr>",
        trade_head=head_html or "<th>no trades yet</th>",
        trade_rows=body_html,
    )


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8050
    print(f"[DASHBOARD] GOLD REAPER APEX console -> http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
