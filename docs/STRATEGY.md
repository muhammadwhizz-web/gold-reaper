# Strategy — REAPER-X

Forged on 20 years of XAU/USD recon (5,032 daily bars, 2006→2026) and tuned
by a 4-fold walk-forward optimizer ranked by **worst** fold, not best.

## Market findings (reproducible)

```bash
python data/fetch_history.py && python features/build_features.py
```

- Gold compounded from $578 → $4,211 across the sample (+7.28x)
- Largest hourly ranges concentrate in the **London/NY overlap, 12:00–16:00
  UTC** (0.60% avg vs 0.33% late US)
- Most explosive hours: 13:00–14:00 UTC
- First backtest split the verdict cleanly: London-only lost (−$2,636) while
  the overlap paid (+$1,682) → the bot hunts the overlap only

## Rules (H4 bias → H1 execution)

1. **BIAS** — H4 close vs EMA50 vs EMA200 → LONG / SHORT / STAND DOWN
2. **TRAP** — price pulls back within 0.6×ATR of EMA20
3. **RESET** — RSI(14) reload zone: longs 38–52, shorts 48–62
4. **KILL** — body candle re-ignites with the bias and ADX ≥ 24
5. **RISK** — SL 1.2×ATR · TP 3.2×ATR (R:R 2.67) · breakeven +1R · ATR trail +1.5R
6. **FILTER** — US-data blackouts (12:25/13:25 UTC ±10m), Friday cutoff,
   weekend lock

## APEX-X ensemble (v2)

Regime-routed specialists vote; consensus is required:

| Module | Speaks in | Logic |
|---|---|---|
| TREND | TREND_UP/DOWN | REAPER-X rules above |
| MEANREV | RANGE | BB(20,2) ≤ 0.05 + RSI < 30 + z < −1.5 (mirrored) |
| BREAKOUT | VOLATILE_CHOP | Donchian(20) break + OFI > 0.05 + vol z > 0.5 |
| NEWS | live, post-event | momentum 5–30 min after relevance ≥ 0.6 release |
| META-MODEL | always | XGBoost soft vote ±0.5, hard gate auto-arms only if OOS AUC ≥ 0.56 |

Consensus: weighted votes ≥ 2.5 **and** ≥ 2 rule modules on one side.
CRISIS regime: nobody speaks.

## ML findings (published, not hidden)

| Experiment | OOS AUC | Verdict |
|---|---|---|
| H1, 6 label geometries (`research/sweep_labels.py`) | 0.500 | no edge |
| D1, 20 years (`research/sweep_daily.py`) | 0.537 | no edge |
| Production gate (`ml/train_meta.py`) | 0.466 | rejected |

Tabular ML on single-asset OHLCV does not clear the bar out-of-sample on this
data. The system therefore leans on regime routing + rule modules and keeps
the ML gate honest. If a future retrain clears the gate, the hard veto arms
automatically — no code change required.

## Backtest verdict (`research/backtest_apex.py`)

Canonical engine: stricter than v1 — no same-bar re-entry, slippage both
sides, SL-first fills, expanding-window ML per fold.

```
metric                     REAPER-X        APEX-X
trades                         48             36
net (USD)                   -970.12        -736.40
win rate %                   33.33          36.11
max DD %                     -14.17         -11.97
blocks ≥ $20 %               27.08          27.78
```

Honest reading: the ensemble reduces losses 24% and drawdown 2.2 points on a
window that contains the 2026 regime break, but both systems lose there. The
v1 engine (`research/optimize.py`) reported +14.2% / PF 1.70 on the same
config; the stricter engine does not reproduce that full-window. Regime
dependence is real — hence paper-first protocol and latched breakers.
