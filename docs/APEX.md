# ☠️ GOLD REAPER APEX — Architecture & Operations

> **Honesty clause:** no component of this system guarantees profit. Every number in
> this document is reproducible with one command. Where the math said "no edge",
> we documented "no edge" — in public.

---

## 1. The 10-Dimension Decision Engine

Every bar is measured across ten dimensions before the ensemble votes:

| # | Dimension | Source | Examples |
|---|-----------|--------|----------|
| 1 | Price/trend | `features/build_features.py` | EMA/WMA/HMA family, MACD, Stoch, ADX/DMI, Supertrend, PSAR, Ichimoku, KST, Coppock, TRIX, Fisher, Aroon, Vortex, UO, AO, CCI, %R, Elder, Force, DPO, EOM, Mass, Chande, BB, Keltner, Donchian |
| 2 | Volume/flow | same | OBV slope, MFI, CMF, delta, cum-delta, order-flow imbalance, VWAP distance, volume z-score |
| 3 | Volatility | same | ATR percentile, realized vol (3 windows), Parkinson, Rogers-Satchell, Garman-Klass, Yang-Zhang, vol-of-vol, BB/KC width |
| 4 | Market structure | same | swings, BOS, CHoCH, FVG, order blocks, liquidity sweeps, fib distances, S/D zones |
| 5 | Statistical | same | z-score, Hurst, autocorrelation, entropy, skew, kurtosis |
| 6 | Cross-asset | yfinance 3y daily | rolling corr/beta/residual-z + Engle-Granger cointegration p-value vs DXY, US10Y, SPX, VIX, silver, oil, BTC |
| 7 | Time/session | `core/sessions.py` | session one-hots, cyclical hour, dow, month, quarter, Friday flag |
| 8 | News/event | `core/news_brain.py` + calendar table | minutes to/next gold-critical event, relevance, sentiment score |
| 9 | Microstructure | features | range ratio, gaps, realized spread (tick feed where available) |
| 10 | Risk | `core/risk_apex.py` | equity curve, drawdown, streaks, block state, breaker latches |

**142 features × 13,722 hourly bars, 98.1% non-NaN coverage** — rebuild anytime:

```bash
python data/ingest_multi_tf.py && python features/build_features.py
```

## 2. Data Layer

| Layer | Tool | Depth achieved in this build |
|---|---|---|
| Ticks | `data/ingest_ticks.py` — Dukascopy `.bi5` LZMA decoder (real institutional feed) | sandbox network 503s the feed; runs on residential/VPS networks, 20y archive available |
| 1m–30m | `data/ingest_multi_tf.py` (yfinance) | 7d of 1m, 60d of 2m/5m/15m/30m (Yahoo's hard limits) |
| 1h/4h | same | 2.4y / derived |
| 1d/1wk/1mo | same | 20y / 26y / 26y |
| Storage | `features/store.py` — DuckDB (columnar, idempotent upserts, Parquet export) | 65k+ bars, 12+ tables |
| Calendar | `data/news_ingest.py` — ForexFactory weekly JSON, cached to disk | live 83 events |

## 3. Regime Router — `core/regime.py`

Gaussian HMM (hmmlearn) over [returns, ATR%, ADX] with a deterministic
state→regime interpreter and a full rule-based fallback (identical interface):

```
TREND_UP · TREND_DOWN · RANGE · VOLATILE_CHOP · CRISIS
```

Routing: trend module speaks in trends, mean-reversion in ranges, breakout in
volatile chop, nobody speaks in CRISIS.

## 4. APEX-X Ensemble — `core/strategy_apex.py`

- **TREND** — Reaper-X pullback logic (kept intact, it is the proven core)
- **MEANREV** — BB(20,2) ≤ 0.05 + RSI < 30 + z < −1.5 (mirrored for shorts), RANGE only
- **BREAKOUT** — Donchian(20) break + |OFI| > 0.05 + volume z > 0.5, VOLATILE_CHOP only
- **NEWS** — momentum 5–30 min after a relevance ≥ 0.6 release, sentiment-confident, live only
- **META-MODEL** — XGBoost soft vote ±0.5; hard gate armed only if a production
  model ever passes the OOS gate (AUC ≥ 0.56)

Consensus: weighted votes ≥ 2.5 **and** ≥ 2 rule modules on the same side.
Risk geometry (locked by the v1 walk-forward): SL 1.2×ATR · TP 3.2×ATR (2.67R)
· BE at +1R · ATR trail at +1.5R · TP ladder 1R / 2.67R / 4R.

## 5. The ML Verdict (read this before trusting any bot)

We trained XGBoost on 142 features with triple-barrier labels under strict
time splits. Results are published, not hidden:

| Experiment | OOS AUC | Verdict |
|---|---|---|
| H1 bars, 6 label geometries, 4 reg settings (`research/sweep_labels.py`) | 0.500 | **no edge** |
| D1 bars, 20 years (`research/sweep_daily.py`) | 0.537 | **no edge** |
| Production gate (`ml/train_meta.py`) | 0.466 | **REJECTED by gate** |

This matches the efficient-market literature: tabular ML on single-asset OHLCV
rarely clears the bar out-of-sample. **Consequence in engineering:** the ML gate
runs as a transparent soft vote only; the ensemble leans on regime routing +
rule modules (which demonstrably reduce drawdown), and the promotion gate stays
strict. If a future retrain ever clears AUC ≥ 0.56, the hard gate arms itself
automatically.

## 6. Walk-Forward Ensemble Backtest — `research/backtest_apex.py`

Canonical engine: stricter than v1 (no same-bar re-entry, slippage both sides,
SL-first fills), expanding-window ML probabilities per fold (zero leakage).

```
════════════════════════════════════════════════════════════════════
 metric                      REAPER-X            APEX-X
----------------------------------------------------------------------
 trades                            48                36
 net (USD)                     -970.12           -736.40
 win rate %                      33.33             36.11
 profit factor                    0.66              0.63
 max DD %                        -14.17            -11.97
 4h blocks active                  48                36
 blocks >= $20 %                  27.08             27.78
════════════════════════════════════════════════════════════════════
```

**Honest reading:** on the hardest 2.4-year window (which contains the 2026
regime that broke the v1 edge), APEX-X *reduces* the baseline's loss by 24%,
cuts max drawdown by 2.2 points, and filters 12 bad trades — but both systems
lose. The strategy family is regime-dependent. The NEWS module abstains in
backtests (no reproducible historical calendar); live, the ensemble has one
more voter. **This is exactly why the bot ships in paper mode and why the
circuit breakers exist.**

The earlier v1 run (`research/backtest.py` + `optimize.py`) showed +14.2% with
PF 1.70 on the same config through its own engine; the stricter engine does not
reproduce that edge full-window. Both engines agree on the lesson: trade the
regimes that pay, stand down when they don't, survive everything else.

## 7. The $20/4h Block Engine — `core/risk_apex.py`

```
┌────────────────────────────────────────────────────────────────┐
│ adaptive sizing   risk% = clamp(0.5%..2%, 20 / (avg_R × eq))   │
│ block tracker     UTC 4h blocks; ≥ $20 banked → block CLOSED   │
│ trade budget      max 3 attempts per block, then stand down    │
│ confidence floor  ensemble confidence ≥ 0.55 required          │
│ rolling PF guard  30d PF < 1.2 → risk × 0.5 until PF ≥ 1.3     │
│ recovery mode     losing day → next blocks at 50% size         │
│ circuit breakers  day −3% / week −7% / month −15% → LATCHED    │
│                   (manual reset: python bot.py --reset-breakers)│
└────────────────────────────────────────────────────────────────┘
```

The sizing formula means a single clean win banks the block target — while the
clamps make it impossible for the bot to size its way out of a losing streak.

**Math reality check:** at 2% risk and avg_R 2.67, banking $20/4h needs equity
≈ $375+. Below that, the block target remains an aspiration, not a mechanic.
Above it, every mechanism above pushes the probability of "green block" up
while capping the cost of a red block.

## 8. Path to $1,000,000 — mechanically possible, never promised

| Stage | Equity | Risk/trade | Monthly target | Duration |
|---|---|---|---|---|
| Paper | $0 | 0% | validate ≥ 1.2 PF | 2 weeks |
| Micro live | $500 | 0.5% | 3–5% | 1 month |
| Small live | $2,000 | 1% | 4–8% | 3 months |
| Growth | $10,000 | 1% | 4–8% | 12 months |
| Scale | $50,000 | 1–1.5% | 4–8% | 24 months |
| Apex | $250,000+ | 1% | 3–6% | 36–48 months |

At 6% monthly compounding, $1,000 → $1M in ~10 years; at 8%, ~7 years. The
compounding math breaks the day you blow up — so every line of risk code
exists to prevent that one day. **The real $1M secret is negative survival
probability events, eliminated.**

## 9. Ops

```bash
# one-command reproducibility
python data/ingest_multi_tf.py        # 12 tables of bars
python data/news_ingest.py            # live calendar
python features/build_features.py     # 142 features + labels
python ml/train_meta.py               # gated meta-model
python research/backtest_apex.py      # ensemble verdict
python research/optimize.py           # v1 walk-forward tune
python bot.py --dashboard             # hunt + console :8050
python bot.py --reset-breakers        # manual breaker reset
```

- **Autostart:** systemd user services + timer (Linux) / 3 Task Scheduler
  entries (Windows) — bot, dashboard, weekly retrain. Crash → restart in 30s.
- **Audit:** `data/audit.jsonl` — every decision, dimension state, votes,
  ML prob, veto reason. The black box always survives.
- **Alerts:** Telegram / Discord / Email on kills, targets, breakers, crashes.
- **Multi-account:** `APEX_ACCOUNTS="MT5|login|pass|srv|acc1;PAPER|||sim"` —
  one hunter thread per account.
- **Broker failover:** MT5 → Bitget → Paper, automatic, per boot and per crash.

## 10. What APEX deliberately does NOT do

1. It does not claim 99% analytical coverage *accuracy* — it measures ~150
   dimensions and publishes which ones carried signal (hint: regime routing
   and trend structure; not tabular ML).
2. It does not promise $20/4h — it engineers the conditions where a $20 block
   is achievable and stops when the market says no.
3. It does not trade the news blind — it waits for the reaction, measures
   sentiment, and sizes down.
4. It does not hide losing regimes — the audit trail and the docs above are
   the receipts.
