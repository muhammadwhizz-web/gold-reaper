# GOLD//REAPER — Demo Guide

Three layers of live demo, zero API keys required.

> **Note on video:** chat tools cannot accept or host video files. This repo
> ships the recording pipeline instead — run it locally, upload the MP4 to
> YouTube/X, then paste the link into the README under **WATCH THE DEMO**.

## Layer 1 — Interactive terminal

```bash
pip install rich colorama
python demo/terminal_demo.py
```

Boot sequence → 2s matrix overture → live hunt stream (scans, module votes,
fill, protective moves, target) → session summary. Seeded synthetic feed —
deterministic, reproducible, safe.

## Layer 2 — Web dashboard

```bash
pip install fastapi uvicorn sse-starlette
python dashboard/app.py          # http://localhost:8080
```

Auto-detects a running bot: if `data/apex_risk.json` exists it renders live
state; otherwise a seeded mock session. Includes SSE log tail (seeds recent
audit history, then tails live), SSE state push (named `state` events every
~6s are the primary state source; the poll widens to a 15s fallback once a
push is seen and narrows back to 5s if the stream drops), equity curve with
running-peak drawdown shading, position card fed by the bot's heartbeat
position snapshot (BE and TRAIL chips, age tooltip), trades table with
R-multiple column, CSV export and a click-through trade detail drawer,
trade analytics (profit factor, win rate, expectancy, max drawdown, PnL
distribution histogram, underwater curve, per-regime split), an
r-multiple band inside every trade drawer (-1R stop .. entry .. +2R
target with a glowing marker at the trade's own R), session-boundary
equity marks on the curve (the bot snapshots real equity at every
session transition into `data/equity_marks.csv`; the console switches
to a real HH:MM UTC axis with a glowing dot per mark — the live curve
moves between fills, honestly; a new mark lands as one quiet info
toast, a dashed crosshair rides the curve's hover, and the tooltip plus
the equity chip report the walk's Δ against the session-open baseline),
a transport LED in the stream header
(`PUSH` when SSE drives state, fallback label while polling — drops and
rejoins toast once each, and the console re-dials with a real
exponential backoff — 1s → 30s cap — instead of hammering a dead
socket), a copy-json button (or `c`) inside the trade drawer, hunt-window
clock with next-3-windows countdown (mirrored in the browser-tab title,
with a canvas-drawn favicon LED: pulsing green while the window is open,
amber under standby, red offline), 24 h session map with a live UTC cursor,
news radar with high-impact countdowns, a 14-day paper-ledger heat strip
(bar height = |net|, color = sign), bot-liveness LED (from
`data/heartbeat.json` — stale heartbeat shows an amber "bot stale" chip and
honest awaiting states instead of fabricated regime/broker data), a real
kill-switch, a footer version chip (`state.version`, sourced from
pyproject), and a matrix rain canvas. Keyboard: `s`/`p` standby · `j`/`k` journal ·
`o` open drawer · `e` export csv · `l` log · `d` demo · `?` shortcut help ·
`Esc` closes drawer/overlay · click a trades row for its detail drawer.

The kill-switch is server-side: the top-bar **standby** button (or `POST
/api/standby {"on": true}`) writes `data/standby.flag`; the running bot
skips NEW entries while the flag exists (exits/management keep running).
State is authoritative from the flag file, so every console and `GET
/api/standby` agree.

Honesty contract: in live mode an empty journal renders empty states
("no fills yet") and a flat equity line at the real balance - never mock
rows or a fake curve. Regime/dimension panels render "awaiting" until the
bot's heartbeat supplies real classified data. Endpoints: `/api/state`,
`/api/trades`, `/api/metrics`, `/api/track_record`, `/api/news`,
`GET+POST /api/standby`, `/api/stream`.

## Layer 3 — Record the video

```bash
pip install asciinema
cargo install agg            # or grab a release binary
apt install ffmpeg           # or brew install ffmpeg
bash demo/record_demo.sh
```

Outputs:

| File | Purpose |
|---|---|
| `demo/gold-reaper-demo.cast` | asciinema cast — embeds natively on GitHub issues/READMEs via asciinema player |
| `demo/gold-reaper-demo.gif`  | README embed |
| `demo/gold-reaper-demo.mp4`  | YouTube/X upload (1080p, faststart) |

Then:
1. Upload the MP4 (title: "GOLD//REAPER — autonomous XAU/USD system demo").
2. Paste the URL into the README `WATCH THE DEMO` button.
3. Optionally commit the `.cast` so GitHub renders it inline.

## Layer 4 — GitHub Pages

The `Pages Demo` workflow builds a static, backend-free dashboard (mock data
embedded) and deploys it to:

```
https://muhammadwhizz-web.github.io/gold-reaper/
```

Requires Pages enabled once: **Settings → Pages → Source: GitHub Actions.**
After the first deploy, the README `▶ LIVE DEMO` badge resolves automatically.

## Deterministic artifact pipeline (no agg required)

`demo/make_cast.py` writes the asciinema-v2 `.cast` directly from the demo
(stdout intercepted, seeded, wall-clock rhythm preserved), and
`demo/render_gif.py` replays it into a branded GIF with an outro card —
Pillow only, no external tools. `ffmpeg` finishes the set:

```bash
python demo/make_cast.py       # demo/gold-reaper-demo.cast
python demo/render_gif.py      # demo/gold-reaper-demo.gif
ffmpeg -i demo/gold-reaper-demo.gif -movflags +faststart -pix_fmt yuv420p \
       demo/gold-reaper-demo.mp4
```

Windows: `powershell -ExecutionPolicy Bypass -File demo\record_demo.ps1`
(uses asciinema/agg when installed, falls back to the deterministic renderer).
Committed artifacts: `demo/gold-reaper-demo.{cast,gif,mp4}` — regenerated
byte-identically every time.
