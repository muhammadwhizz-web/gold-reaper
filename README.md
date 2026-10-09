<div align="center">

<img src="docs/banner.png" width="100%" alt="GOLD//REAPER — autonomous XAU/USD trading system"/>

# GOLD//REAPER

### Autonomous XAU/USD trading system · walk-forward validated · dark-terminal ops

[![CI](https://img.shields.io/badge/CI-blood_test-00FF9C?style=flat-square&labelColor=0A0E0F)](https://github.com/muhammadwhizz-web/gold-reaper/actions)
[![Python](https://img.shields.io/badge/python-3.12-00D9FF?style=flat-square&labelColor=0A0E0F)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-5A6B6F?style=flat-square&labelColor=0A0E0F)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/muhammadwhizz-web/gold-reaper/main?style=flat-square&labelColor=0A0E0F&color=00FF9C)](https://github.com/muhammadwhizz-web/gold-reaper/commits/main)
[![Backtest](https://img.shields.io/badge/backtest-walk--forward-FFB000?style=flat-square&labelColor=0A0E0F)](docs/STRATEGY.md)

[▶ LIVE DEMO](https://muhammadwhizz-web.github.io/gold-reaper/) · [▶ WATCH THE DEMO](docs/DEMO.md) · [docs](docs/ARCHITECTURE.md)

`gold trading bot` · `xauusd bot` · `metatrader5 python` · `bitget ccxt` ·
`algorithmic trading python` · `walk-forward backtest` · `autonomous trading system`

</div>

---

## What it is

GOLD//REAPER is an autonomous **XAU/USD (gold) trading system** written in
Python 3.12 that ingests 20 years of market history, measures **142 features
across 10 dimensions** — indicators, volatility models, market structure,
cross-asset cointegration, news and sentiment — and trades through a
regime-routed ensemble called **APEX-X**. Execution runs on **Exness via
MetaTrader 5**, **Bitget futures via ccxt**, or a built-in paper simulator,
24/7, under systemd or Task Scheduler with latched circuit breakers between
the market and your account. Every decision is recorded to an append-only
audit trail, every claim in this README is reproducible with one command, and
the backtest verdict — including the losing regimes — is published below.

## Status

```text
┌─ reaper ──────────────────────────────── 12:04:33 UTC ─┐
│ session  london/ny overlap   ● HUNTING                 │
│ equity   10,000.00 USD       +0.42% today              │
│ open     1 x XAUUSD long     2,418.60 → TP 2,431.20    │
│ guard    daily -3% · session +$20 · streak 0           │
└────────────────────────────────────────────────────────┘
```

## Architecture

```text
data acquisition        features + cognition          decision            execution
─────────────────       ────────────────────          ──────────          ─────────
yfinance 1m→1mo    ─┐   142 features · 10 dims        APEX-X ensemble     MT5 (exness)
dukascopy ticks    ─┼─▶ HMM regime router        ──▶ consensus vote  ──▶ bitget (ccxt)
forexfactory news  ─┘   news + sentiment               risk gates          paper sim
        │                  │                              │                   │
        └──────── duckdb store ── audit.jsonl ◀──────────┴───────────────────┘
                                 (append-only black box)
```

Full module map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Strategy — REAPER-X + APEX-X ensemble

Forged on 20 years of gold data and tuned by a **4-fold walk-forward
optimizer** ranked by worst fold (robustness over glory):

```text
recon findings:            gold $578 → $4,211 (+7.28x)
                           kill zone: london/ny overlap 12-16 UTC (0.60% hr range)

REAPER-X (trend module):   h4 bias (ema50/ema200)
                           h1 pullback to ema20 (0.6x atr)
                           rsi reload zone (L 38-52 · S 48-62)
                           body-candle trigger + adx ≥ 24
                           SL 1.2x atr · TP 3.2x atr · BE +1R · trail +1.5R

APEX-X (v2 ensemble):      regime-routed modules vote:
                             TREND (reaper-x) · MEANREV (bb+rsi+z)
                             BREAKOUT (donchian+flow) · NEWS (post-event)
                           consensus ≥ 2.5 weight + ≥ 2 rule modules
                           xgboost soft vote ±0.5 (hard gate if AUC ≥ 0.56)
```

Details and findings: [docs/STRATEGY.md](docs/STRATEGY.md)

## Backtest verdict — the honest section

Canonical walk-forward engine (`research/backtest_apex.py`): no same-bar
re-entry, slippage both sides, SL-first fills, expanding-window ML per fold.

```text
metric                     REAPER-X        APEX-X
trades                         48             36
net (USD)                   -970.12        -736.40
win rate %                   33.33          36.11
profit factor                 0.66           0.63
max drawdown %               -14.17         -11.97
4h blocks ≥ $20 %            27.08          27.78
```

- APEX-X **reduces the baseline's loss by 24%** and max drawdown by 2.2
  points on a window containing the 2026 regime break — but both systems
  lose there. Regime dependence is real and published.
- Tabular ML (XGBoost, 142 features) did **not** clear out-of-sample AUC 0.56
  on hourly (0.500) or daily (0.537) gold — the promotion gate rejected it.
  The ML gate therefore runs as a transparent soft vote and arms itself only
  if a future retrain clears the bar.
- An earlier, less strict engine reported +14.2% (PF 1.70) on the same
  config; the stricter engine does not reproduce that full-window. Both
  engines agree on the lesson: trade regimes that pay, stand down when they
  don't.

Reproduce: `python data/ingest_multi_tf.py && python features/build_features.py && python research/backtest_apex.py`

## Installation

### Linux (systemd autostart)

```bash
git clone https://github.com/muhammadwhizz-web/gold-reaper.git
cd gold-reaper
chmod +x install_linux.sh && ./install_linux.sh
cp .env.example .env && nano .env
systemctl --user start gold-reaper
tail -f data/reaper.log
```

Installs the bot service (boot-persistent, crash-restart 30s), the ops
dashboard (:8050), and a weekly retrain timer.

### Windows (Task Scheduler autostart)

```powershell
git clone https://github.com/muhammadwhizz-web/gold-reaper.git
cd gold-reaper
powershell -ExecutionPolicy Bypass -File install_windows.ps1
notepad .env
Start-ScheduledTask -TaskName GOLD-REAPER
```

Registers three boot-persistent tasks: bot, dashboard (:8050), weekly retrain.

Going live later: `BROKER=MT5` (or `BITGET`) + `PAPER_MODE=false` in `.env`.
Protocol: **2 weeks paper → 0.01 lots → scale.** Risk contract:
[docs/RISK.md](docs/RISK.md)

## Live demo — three layers

| Layer | Command | What you get |
|---|---|---|
| Terminal | `python demo/terminal_demo.py` | boot sequence, matrix overture, simulated hunt with fill + protective moves + session summary |
| Web | `python dashboard/app.py` → :8080 | equity curve, position card, PnL, trades, SSE log tail, matrix canvas — attaches to a live bot when present, mock session otherwise |
| Static | [▶ LIVE DEMO](https://muhammadwhizz-web.github.io/gold-reaper/) | backend-free GitHub Pages build |

Record your own video: `bash demo/record_demo.sh` →
`.cast` + `.gif` + `.mp4` (asciinema → agg → ffmpeg). Guide:
[docs/DEMO.md](docs/DEMO.md)

## Roadmap

- [x] v1 — REAPER-X core, walk-forward tuning, brokers, autostart
- [x] v2 — APEX stack: 10-dimension features, regime router, ensemble,
      block risk engine, audit, alerts, dashboard
- [x] v2.1 — professional rebrand, demo suite, GitHub Pages
- [ ] v2.2 — OANDA/IBKR broker adapters, order-flow tick features from the
      Dukascopy layer, per-regime parameter sets
- [ ] v3 — meta-model OOS gate cleared → hard ML veto arms automatically;
      portfolio mode (XAU + correlated assets)

## Risk disclaimer

Trading leveraged gold (CFDs, futures, tokenized metals) carries a
substantial risk of loss and is not suitable for every investor. This
software is educational, ships in **paper mode**, and publishes losing
regimes alongside winning ones. Past performance — backtested or live — does
not guarantee future results. You are solely responsible for your capital.
Read [docs/RISK.md](docs/RISK.md) before enabling live execution.

## License & credits

MIT — see [LICENSE](LICENSE). Built with Python 3.12, pandas, NumPy,
DuckDB, XGBoost, hmmlearn, statsmodels, ccxt, MetaTrader5, FastAPI, rich.

<details>
<summary>Repository map</summary>

```text
bot.py · core/ (strategy, strategy_apex, regime, news_brain, risk, risk_apex,
audit, notify, sessions, indicators, config, logger, dashboard)
brokers/ (mt5, bitget, paper) · features/ (build_features, store)
ml/ (train_meta, retrain_schedule) · research/ (backtest_apex, backtest,
optimize, sweep_labels, sweep_daily) · data/ (ingest_multi_tf, ingest_ticks,
news_ingest, fetch_history) · demo/ (terminal_demo, record_demo.sh)
dashboard/ (app, static) · docs/ · assets/ · .github/workflows/
```
</details>
