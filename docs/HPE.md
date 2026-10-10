# HPE — High-Probability Edge ensemble (spec + measured results)

> Built 2026-10-10 on the repo's DuckDB store: **13,733 H1 GC=F bars,
> 2024-05-17 → 2026-10-09**. Every number below is measured, not projected,
> and reproducible with the commands in §6. The headline result is negative
> and published up front: **at the spec voting rule the ensemble took zero
> trades in 2.4 years**, so HPE ships DISARMED (shadow mode). Nothing here is
> a promise of profit. See [RISK.md](RISK.md).

## 1. What HPE is

HPE is an additive decision layer on top of the frozen REAPER-X/APEX-X stack.
Eight independent modules vote on every candidate bar; a trade fires only
when **>= 4 of 8 align on one direction AND psychology raises no veto AND the
regime fits AND the session fits**.

Geometry (evidence-based, clamped in code — `core/strategy_hpe.py`):

- SL `1.2 x ATR(14)`, TP `2.2R` — inside the 2.0-2.5R evidence band
  (`HPE_TP_R` is hard-clipped to `[2.0, 2.5]`, non-negotiable)
- breakeven at `+1R`, ATR trail from `+1.5R`
- conservative fills in research: spread 0.35 + slippage 0.05 both sides,
  SL checked first, same-bar both-hit resolves SL-first

Two-pass voting (`core/ensemble_hpe.py`): the seven proposer modules
(trend, meanrev, breakout, psychology, cross_asset, news, ml) vote first and
produce a directional pre-lean; the **micro-structure module then CONFIRMS the
pre-lean** from candle anatomy + path metrics — it never invents direction.
This implements Phase 3's "must confirm" semantics. Alignment is counted over
all 8 votes; confidence is a documented heuristic (agreement + aligned
conviction + psychology coverage), floor 0.40.

## 2. The eight modules — measured fire rates

Candidate bars: every bar in the replay loop that passes the session/hour
gate (default overlap 12-15 UTC), the Friday cutoff, the crisis gate and the
risk gate — **2,354 candidate bars**. Fire rate = bars where the module cast
a non-zero vote. From the same replay:

| module | fires | rate | vote rule (one line) |
|---|---:|---:|---|
| trend | 50 | 2.1% | bias-up/down pullback reload, body candle confirms |
| meanrev | 43 | 1.8% | RANGE regime band-touch reversion (BB / RSI / z100) |
| breakout | 203 | 8.6% | Donchian(20) break + order-flow imbalance in active regimes |
| psychology | 428 | 18.2% | contrarian: fade euphoria, buy capitulation, 30/70 zones |
| micro | 344 | 14.6% | candle anatomy + path confirm the pre-lean (see note) |
| cross_asset | 569 | 24.2% | gold residual vs DXY/SILVER stretched beyond ±1.2 sigma |
| news | 0 | abstains | headline sentiment — no reproducible source, stands down |
| ml | 0 | never voted | P(TP-first) >= 0.60 long-edge soft vote — gate refused |

Module inputs, for the record: trend reads the H4 EMA50/200 bias, a pullback
within 0.6xATR of EMA20, the RSI(14) reload zone, ADX >= 24 and a body candle
(frozen REAPER-X logic, mirrored read-only). meanrev needs BB(20,2) position
<= 0.05 / >= 0.95, RSI < 30 / > 70 and z100 beyond ±1.5 in RANGE only.
breakout needs the Donchian break, an OFI sign and vol_z >= 0.3, in
TREND_*/VOLATILE_CHOP only. cross_asset requires abs(corr) > 0.2 against the
anchor. Full vote logic lives in `core/ensemble_hpe.py`, which every decision
log quotes verbatim.

Honesty notes, diagnose-style:

- **micro** is a confirmer, not an initiator. It was *asked* to confirm on
  1,039 candidate bars (44.1% had a directional pre-lean), agreed on anatomy
  374 times, and cleared the path gate to cast 344 votes (14.6%). It cannot
  manufacture a lean on a quiet bar.
- **news abstains** — there is no reproducible historical sentiment source to
  replay, so the module stands down for the whole sample rather than
  fabricating a series. In live mode it speaks only with a wired source.
- **ml never voted** — across all out-of-sample bars the calibrated model
  never reached the 0.60 long-edge threshold (max OOS probability 0.5455, 0
  bars taken). The gate refused; see §4.

## 3. Files delivered

| file | role |
|---|---|
| `features/micro_features.py` | 48 `mf_` columns; no-lookahead check passes at k=10,000 |
| `core/psychology.py` | 27 `psy_` columns — [PSYCHOLOGY.md](PSYCHOLOGY.md) |
| `core/ensemble_hpe.py` | 8-module two-pass voting, full decision telemetry |
| `core/strategy_hpe.py` | broker-agnostic strategy, HPE geometry, env config |
| `core/risk_hpe.py` | survival risk layer, additive over the frozen stack — §5 |
| `ml/train_hpe.py` | purged walk-forward trainer + promotion gate |
| `ml/explain_hpe.py` | SHAP-or-fallback attribution + vote-matrix formatting |
| `research/backtest_hpe.py` | the honesty instrument: full-stack zero-leakage replay |
| `research/regime_report.py` | win rate by regime / session / hour / setup signature |

Tests: **62 HPE tests** across 6 files (`test_micro_features` 8,
`test_psychology` 7, `test_ensemble_hpe` 12, `test_strategy_hpe` 14,
`test_risk_hpe` 12, `test_ml_hpe` 9). Full repo suite: **145 passed, 0
failures**. The 6 frozen files are byte-identical
(`docs/PROTECTED_HASHES.txt`, CI-enforced).

## 4. Walk-forward results — the honest core

### ML (XGBoost, 216 features, 3 OOS folds, purge + embargo 24 bars)

| fold | fit rows | OOS AUC | taken @ 0.60 | PF (taken) |
|---|---:|---:|---:|---:|
| 0 | 2,708 | 0.5049 | 0 | n/a |
| 1 | 5,454 | 0.4711 | 0 | n/a |
| 2 | 8,200 | 0.4726 | 0 | n/a |

Gate: **pooled AUC >= 0.58 AND worst-fold PF >= 1.2** (folds with < 5 taken
signals fail — no tiny-sample flukes). Result: mean AUC 0.4829, zero taken
signals in every fold → **GATE REJECTED**. Artifact
`ml/models/hpe_production.json` ships with `"armed": false`. The ML stays a
soft vote that never speaks — no long edge was found out-of-sample. This is
now the third independent ML rejection on this data (hourly 0.500, daily-20y
0.528, HPE 216-feature 0.4829).

### Ensemble walk-forward

**Zero trades in 2.4 years at the spec rule (>= 4/8)** — in the default
overlap window (2,354 candidates) and in the measured-top-8-hours variant
(`data/hpe_report_hours8.json`, 3,818 candidates across hours
0/13/17/19/20/21/22/23 UTC). Both reports: `trades: 0`, `armed: false`.

Alignment histogram (modules voting the candidate side):

| window | candidates | 0/8 | 1/8 | 2/8 | 3/8 | 4/8 |
|---|---:|---:|---:|---:|---:|---:|
| default 12-15 UTC | 2,354 | 1,315 | 623 | 394 | 22 | 0 |
| top-8-hours variant | 3,818 | 2,406 | 888 | 510 | 14 | 0 |

Max alignment observed: **3/8** — 22 candidate bars (18 distinct runs) in the
default window, 14 in the 8-hour variant. A diagnostic scan across every
scannable bar in the sample (13,054 bars, all hours, same gates otherwise)
found exactly **one** 4/8 bar in 2.4 years — 2025-12-22 05:00 UTC, a SHORT
that the session gate vetoed (ASIA), exactly as designed.

**Why:** with news and ml honestly silent, 7 modules are live, and 4/8 asks
blocs with different personalities to agree. Measured on the 22 top-alignment
bars (default window), the persistent co-voter is **cross_asset** — present in
all 22 vote compositions — most often paired with breakout + micro (15 of
22). The capitulation/euphoria co-fires the build expected are rarer still:
psychology votes the side in 5 of 22, and a capitulation or euphoria flag is
set in 7 of 22. The intended reversal setups exist in the data, but at this
frequency target they are barely reachable — exactly what the histogram says.

**Verdict:** HPE ships **DISARMED (shadow mode)** — full decision telemetry,
zero orders. This satisfies the house rule *never ship a config with a
negative worst-fold*: there is no walk-forward evidence of edge, so nothing
trades. The alignment histogram and every losing ML fold are published, not
hidden.

## 5. Survival risk layer (`core/risk_hpe.py`)

Additive over the frozen risk stack (`core/risk.py`, `core/risk_apex.py`):
it can only shrink or block, never grow.

- confidence-scaled sizing 0.3% .. 1.5% of equity (ramp from 0.40 confidence)
- regime-scaled: TREND 1.0 / RANGE 0.8 / VOLATILE_CHOP 0.6 / CRISIS 0
- equity-curve pause: no entries while equity sits below its own 50-trade mean
- 1/4-Kelly cap from the rolling ledger; a negative-edge ledger **blocks**
  trading outright instead of sizing down to a token risk
- loss-streak brake: 2 consecutive losses -> 50% size until a win
- session target: +$20 in a 4h block -> done for that block
- hard stops: daily -2% / weekly -5% / monthly -10%

## 6. How to run

```bash
python research/backtest_hpe.py                 # spec default (12-15 UTC)
HPE_REPORT_TAG=_hours8 HPE_ENTRY_HOURS=0,13,17,19,20,21,22,23 \
  python research/backtest_hpe.py               # measured-top-8-hours variant
python research/regime_report.py                # breakdown of any trades
python ml/train_hpe.py                          # ML + gate (exit 2 = rejected)
```

Every number in this document comes from these commands plus
`data/hpe_report.json`, `data/hpe_report_hours8.json`,
`data/regime_report.json` and `ml/models/hpe_production.json`.

## 7. Calibration disclosure

- Module sensitivities (psychology voting zones 30/70, cross-asset stretch
  1.2 sigma, breakout vol_z 0.3) were set **once**, after a fire-rate
  diagnostic on the FULL sample. No per-fold tuning was performed anywhere.
- The alignment histogram above is the complete record — nothing is filtered
  out.
- The ML gate (AUC >= 0.58, worst-fold PF >= 1.2, min 5 taken) was fixed
  before training and was untouched by any tuning; it rejected.

Hourly EV, unconditional triple-barrier at HPE geometry (TP 2.64 ATR /
SL 1.2 ATR / 24h horizon; SL-first fills; timeout = realized move / 1R).
Units: R, where a stop is -1R and the target +2.2R. `ev_both` is the mean of
the long-side and short-side EVs — "is this hour tradable in either
direction on average". Entry at the bar's close, no costs.

| hour (UTC) | ev_long | ev_short | ev_both | | hour (UTC) | ev_long | ev_short | ev_both |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 00 | +0.1476 | +0.0378 | +0.0927 | | 12 | -0.0337 | +0.0551 | +0.0107 |
| 01 | +0.0904 | -0.0170 | +0.0367 | | 13 | +0.0568 | +0.0063 | +0.0316 |
| 02 | +0.1128 | -0.0346 | +0.0391 | | 14 | +0.0309 | -0.1055 | -0.0373 |
| 03 | +0.0887 | -0.0575 | +0.0156 | | 15 | +0.0703 | -0.1049 | -0.0173 |
| 04 | +0.0279 | +0.0105 | +0.0192 | | 16 | +0.1066 | -0.0515 | +0.0275 |
| 05 | +0.1104 | -0.0493 | +0.0306 | | 17 | +0.1401 | -0.0314 | +0.0543 |
| 06 | +0.0171 | -0.0962 | -0.0395 | | 18 | +0.1593 | -0.0881 | +0.0356 |
| 07 | +0.0751 | +0.0054 | +0.0403 | | 19 | +0.1706 | -0.0933 | +0.0387 |
| 08 | +0.0010 | -0.0054 | -0.0022 | | 20 | +0.1279 | -0.0506 | +0.0387 |
| 09 | +0.0049 | +0.0058 | +0.0054 | | 21 | +0.1967 | -0.0865 | +0.0551 |
| 10 | -0.0495 | -0.0212 | -0.0353 | | 22 | +0.1123 | +0.0323 | +0.0723 |
| 11 | -0.0471 | +0.0283 | -0.0094 | | 23 | +0.1539 | -0.0254 | +0.0643 |

- Current hunt hours 12-15: ev_both +0.0107 / +0.0316 / -0.0373 / -0.0173.
  Best hours by ev_both: **0 (+0.093), 22 (+0.072), 23 (+0.064), 21 (+0.055),
  17 (+0.054)**.
- Long-side EV is positive in 21/24 hours; short-side EV is positive in only
  8/24 and barely so. This is a **structural in-sample bias** — the window
  contains a secular gold uptrend — documented as such, **NOT an edge**. It
  must not be read as "longs pay here": unconditional hourly EV is not the
  ensemble's conditional EV, and the hunt window was chosen from realized
  ensemble trade buckets (docs/DIAGNOSIS.md Q2), not from this table.

## 8. Live behavior

- `REAPER_STRATEGY=HPE` is the env contract for selecting the HPE strategy in
  `bot.py` (same `evaluate` / `manage_exit` interface as REAPER-X, so the bot
  drives it identically). Integration status: the strategy, ensemble, risk
  and explain modules plus both arming artifacts are in the tree; the bot.py
  selection hook is the remaining wiring step (bot.py currently instantiates
  REAPER-X directly).
- Default mode is **SHADOW**: every candidate bar logs the full decision
  trail — per-module votes, vetoes and the gate line
  (`ml/explain_hpe.py.format_decision`) — and feeds the same telemetry to the
  dashboard. Zero orders.
- Arming requires **BOTH** artifacts to say `armed: true`:
  `ml/models/hpe_production.json` (ML gate) and `data/hpe_report.json`
  (ensemble walk-forward). **Neither is true today.** Check with:

```bash
python - <<'PY'
import json
for p in ("ml/models/hpe_production.json", "data/hpe_report.json"):
    print(p, "armed =", json.load(open(p))["armed"])
PY
```

## Cross-references

[PSYCHOLOGY.md](PSYCHOLOGY.md) — how the psychology module is measured ·
[WIN_RATE.md](WIN_RATE.md) — why 100% is impossible and what the real ceiling
is · [RISK.md](RISK.md) — survival controls · [DIAGNOSIS.md](DIAGNOSIS.md) —
the published v2.2 baselines this build refuses to dress up.
