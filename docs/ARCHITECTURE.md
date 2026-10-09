# Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          data acquisition layer                          │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐  │
│  │ ingest_multi_tf  │ │ ingest_ticks     │ │ news_ingest              │  │
│  │ yfinance 1m→1mo  │ │ dukascopy .bi5   │ │ forexfactory calendar    │  │
│  │ 65k bars·12 tfs  │ │ lzma tick decode │ │ impact + relevance       │  │
│  └────────┬─────────┘ └────────┬─────────┘ └────────────┬─────────────┘  │
│           └────────────┬───────┴────────────────────────┘                │
│                 features/store.py  ·  duckdb columnar store              │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│                       feature + cognition layer                          │
│  build_features.py          regime.py             news_brain.py          │
│  142 features·10 dims       hmm(5)+rules          event risk·sentiment   │
│  triple-barrier labels      5-state router        lexicon/llm scorer     │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│                          decision layer                                  │
│   strategy_apex.py · APEX-X ensemble                                     │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│   │ trend    │ │ meanrev  │ │ breakout │ │ news     │ │ meta-model    │  │
│   │ reaper-x │ │ bb+rsi+z │ │ donch+of │ │ momentum │ │ xgb soft vote │  │
│   └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬────────┘  │
│        └──────── consensus ≥ 2.5 weight · ≥ 2 rule modules ──────────┘   │
│                             │  apex signal                               │
│   risk_apex.py ─────────────▼────────────  audit.py                      │
│   block +$20/4h · sizing · breakers        jsonl black box               │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│                          execution layer                                 │
│   brokers/ · mt5 (exness) ⇄ bitget (ccxt) ⇄ paper                        │
│   failover chain · server-side SL/TP · magic 666666                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│                          operations layer                                │
│   bot.py 24/7 loop · dashboard :8050/:8080 · notify tg/dc/mail           │
│   systemd + task scheduler · weekly retrain timer · CI + pages           │
└──────────────────────────────────────────────────────────────────────────┘
```

Module map:

| Path | Responsibility |
|---|---|
| `core/config.py` | every dial, `.env` overrides |
| `core/sessions.py` | session clock, blackout, weekend guards |
| `core/indicators.py` | EMA/RSI/ATR/ADX primitives |
| `core/strategy.py` | REAPER-X pullback engine (trend module core) |
| `core/strategy_apex.py` | APEX-X ensemble + trade manager |
| `core/regime.py` | HMM regime router |
| `core/news_brain.py` | event risk + sentiment |
| `core/risk.py` / `core/risk_apex.py` | legacy state / APEX survival layer |
| `core/audit.py` | append-only decision ledger |
| `core/notify.py` | Telegram/Discord/Email |
| `core/dashboard.py` | ops console :8050 (bot-attached) |
| `dashboard/` | demo console :8080 (mock-or-live) |
| `ml/` | meta-model training + weekly retrain |
| `research/` | backtests, optimizer, sweeps |
| `data/` | ingestion pipelines + history |
| `features/` | feature forge + store |
