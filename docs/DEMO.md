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
audit history, then tails live), equity curve, position card, trades table
with CSV export, trade analytics (profit factor, win rate, expectancy, max
drawdown, PnL distribution histogram, underwater curve, per-regime split),
hunt-window clock with next-3-windows countdown, and matrix rain canvas.

Honesty contract: in live mode an empty journal renders empty states
("no fills yet") and a flat equity line at the real balance - never mock
rows or a fake curve. Endpoints: `/api/state`, `/api/trades`, `/api/metrics`,
`/api/track_record`, `/api/stream`.

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
