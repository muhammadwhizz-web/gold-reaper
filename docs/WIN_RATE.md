# WIN RATE — why 100% is impossible, and what the real ceiling is

> Required by the mission. This page exists so nobody has to ask: the honest
> answer to "can the bot win 100% of its trades" is **no, and any system that
> claims it is either hiding losses or not trading.** What follows is the
> arithmetic, the repo's stance, and the measured state of HPE on this
> dataset. Numbers cross-referenced from [HPE.md](HPE.md); survival controls
> in [RISK.md](RISK.md).

## 1. The arithmetic of 100%

- With any stop-loss greater than zero, a losing trade is always possible:
  price can touch the stop before the target on pure noise. The only way to
  remove losing trades is to remove the stop — which removes the definition
  of risk and eventually the account.
- Fees and spread make it worse: with a symmetric reward, the breakeven win
  rate is strictly **above 50%** once costs are paid. A coin-flip system
  bleeds.
- Martingale and averaging-down can lift a *headline* win rate arbitrarily
  high — by hiding ever-larger tail risk in a small number of catastrophic
  losses. Both are forbidden in this repo.
- Therefore: a system claiming 100% is either hiding losses or not trading.
  This one refuses to make the claim.

## 2. Win rate vs expectancy

Expectancy is the only number that pays the bills. At HPE geometry
(TP 2.2R, SL 1R, ignoring the breakeven/trail exits for clarity):

```
EV = WR * 2.2R - (1 - WR) * 1R          breakeven WR = 1 / 3.2 = 31.25%

WR 65%  ->  0.65 * 2.2 - 0.35 * 1  =  +1.08R per trade
WR 40%  ->  0.40 * 2.2 - 0.60 * 1  =  +0.28R per trade
WR 30%  ->  0.30 * 2.2 - 0.70 * 1  =  -0.04R per trade
```

The tension is structural: **the only way to buy win rate is to shrink the
target relative to the stop** — every point of win rate is paid for in R.
Push the target out and the win rate falls; pull it in and each win gets
smaller. There is no configuration that raises both. (Breakeven-at-+1R and
the +1.5R trail split the outcome space into win / scratch / loss, so live
distributions are messier than the two-outcome formula — the formula is the
clean upper bound, not a forecast.)

The repo's honest stance, unchanged since v2.3: **expectancy and survival
first; win rate is reported only as a filtered-subset metric**, never as a
target.

## 3. HPE's filtered-subset philosophy

The `>= 4/8` agreement rule IS the win-rate lever: fewer, cleaner trades —
days with zero trades are correct behavior. Measured reality on 13,733 H1
bars (2024-05-17 → 2026-10-09):

- maximum module alignment ever reached inside the spec entry windows:
  **3/8** — 22 candidate bars of 2,354 (default window), 14 of 3,818
  (top-8-hours variant)
- qualifying trades at the spec rule: **zero in 2.4 years**
- therefore **no measured HPE win rate exists yet — and we refuse to
  manufacture one.**

The full alignment histogram and the per-module fire rates are published in
[HPE.md](HPE.md) §2 and §4. Nothing is filtered out of that record.

## 4. What would have to be true to measure a win rate

1. **Eight live modules.** Either a promoted ML (pooled OOS AUC >= 0.58 AND
   worst-fold PF >= 1.2 — current verdict: mean AUC 0.4829, 0/0/0 signals
   taken, gate REJECTED) or a live news module that actually speaks. Today
   news abstains and the ML never reached its 0.60 long-edge threshold (max
   OOS probability 0.5455).
2. **Consensus episodes.** At least some bars must align >= 4/8 inside the
   entry windows. In 2.4 years, none did. (Across every scannable bar
   regardless of hour, exactly one 4/8 episode occurred — and the session
   gate vetoed it, as designed.)
3. **A shadow ledger with real signal outcomes.** Once trades qualify, the
   shadow mode accumulates what actually happened to them — wins, losses,
   scratches — BEFORE any capital is pointed at the strategy.

All three are currently false. Stated plainly: there is no HPE win rate
today, and the fastest way to get one honestly is the slow way.

## 5. The honest ceiling

- On this dataset, the **unconditional triple-barrier base rate is 35.7%**
  TP-first at HPE geometry (TP 2.64 ATR / SL 1.2 ATR, 24h horizon, SL-first
  fills, 13,709 labelled bars). That is the raw material any filter must
  improve on.
- A filtered subset must clear costs: the research fill model charges
  0.35 spread + 0.05 slippage **per side** — 0.80/oz round trip in the
  ledger, deliberately conservative. At 1.2xATR stops that is a real slice
  of every 1R risked.
- Mean-reversion-flavored setups — the contrarian bloc HPE's consensus is
  built around — historically carry win rates well above trend systems but
  pay for it in tail shape; even a strong filtered subset at this geometry
  tops out **far below 100%**.
- The floor is published and stays published: the v2.2 baselines in
  [DIAGNOSIS.md](DIAGNOSIS.md) — the ensemble **without** ML lost
  **-970.12** (PF 0.66), APEX-X **with** ML lost **-736.40** (PF 0.63) on
  this same window. Those are the numbers this build refuses to dress up.

## 6. Cross-references

- [HPE.md](HPE.md) — module fire rates, alignment histogram, walk-forward
  gate, hourly EV table
- [PSYCHOLOGY.md](PSYCHOLOGY.md) — the veto layer that filters the filtered
  subset further
- [RISK.md](RISK.md) — survival controls: sizing caps, breakers, pauses
- [DIAGNOSIS.md](DIAGNOSIS.md) — the published losing baselines and the
  bucket-level diagnosis behind them
