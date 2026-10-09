# Growth plan — where to post, what to say

> The repo cannot post to social platforms by itself. This file contains the
> ready-to-paste drafts, the honest angles that fit the brand, and the
> checklist. Post manually, one platform per day, and reply to every comment
> for the first 48 hours — that is what actually moves the needle.

## The angle (why anyone should care)

Not "a trading bot". The angle that gets traction with technical audiences:

1. **We published our losing walk-forward.** Most bot repos show green
   equity curves and hide the folds that bleed. This one leads with
   -970 USD and shows the buckets that lose money.
2. **Then we fixed the signal in public** — diagnosis-driven, walk-forward
   gated, worst-fold rule, changes logged in CHANGELOG.md.
3. **The engineering is real**: DuckDB feature store, HMM regime router,
   142-feature forge, expanding-window ML, audit trail, one-click
   installers for three operating systems.

## Platform checklist

| # | Platform | Angle | Status |
|---|----------|-------|--------|
| 1 | r/algotrading | honesty angle | ☐ owner action |
| 2 | r/Python | engineering angle (DuckDB + HMM + FastAPI) | ☐ owner action |
| 3 | Hacker News (Show HN) | "an honest quant bot that publishes its losses" | ☐ owner action |
| 4 | X / Twitter | terminal GIF + diagnosis thread | ☐ owner action |
| 5 | LinkedIn | quant-audience walkthrough | ☐ owner action |
| 6 | GitHub profile | pin the repo, cross-link from other repos | ☐ owner action |
| 7 | YouTube | paste the mp4 (demo/gold-reaper-demo.mp4), link in README | ☐ owner action |

## Draft 1 — r/algotrading

**Title:** I published my losing walk-forward results instead of hiding them — here's what the diagnosis found and how I fixed the signal

**Body:**

I built an autonomous gold (XAU/USD) bot in Python and did something most
bot repos don't: I published the walk-forward backtest where it loses.

The honest numbers first. 13,722 hourly bars, 4 chronological folds,
costs applied both sides, no same-bar re-entry:

- v2.2 config: net -970 USD, profit factor 0.66, max DD -14%
- the worst fold lost -663 USD on its own

Then instead of tuning until it looked green, I wrote a diagnosis script
that buckets every trade:

- by regime: RANGE-regime mean reversion bled -526 (fading a trending tape)
- by hour: 14:00-15:00 UTC cost -1,010 combined; 12:00 was profitable
- by weekday: almost certainly noise, so I deliberately did NOT ship a
  weekday filter
- SL-first vs TP-first fills: identical to the cent — the pessimistic
  assumption wasn't the problem
- costs: real but not the root cause; even zero-cost execution lost

Three changes survived the walk-forward gate (a change only ships if the
WORST fold improves): hunt hours 12-14 UTC only, TP 2.67R → 2.0R,
mean-reversion gated to low-volatility tape.

Result: worst fold -663 → -112, full-period net -970 → +34. Which is...

**8-11 trades. That's an anecdote, not a sample.** A PF of 2.57 on 8 trades
is indistinguishable from luck, and I say that in the README itself. The
arbitration is an append-only paper track record: one row per UTC day, win
or lose, updated automatically.

Everything is reproducible with one command and MIT licensed:
https://github.com/muhammadwhizz-web/gold-reaper

Happy to answer questions about the walk-forward methodology, the feature
store, or the parts that didn't work (tabular ML on gold direction: twice
tested, twice rejected — hourly AUC 0.500, 20-year daily AUC 0.528).

## Draft 2 — r/Python

**Title:** DuckDB + HMM + XGBoost + FastAPI: the stack behind an open-source gold trading bot (with its losses published)

**Body:**

I open-sourced my autonomous XAU/USD trading system and wanted to share the
engineering, because the interesting parts are language-agnostic:

- **DuckDB as a feature store**: one file, idempotent upserts, 12 bar
  tables + 142 engineered features + a ForexFactory calendar. Replaces a
  whole zoo of CSVs.
- **HMM regime router** (hmmlearn): TREND_UP / TREND_DOWN / RANGE /
  VOLATILE_CHOP / CRISIS, with a deterministic rule fallback so the live
  bot never blocks on a model.
- **XGBoost meta-model — and why it's NOT allowed to decide**: expanding-
  window walk-forward gave 0.50 AUC hourly, 0.528 on 20 years of daily
  bars. Twice rejected by the promotion gate. It ships as a transparent
  soft vote and the hard gate only arms if a future retrain clears 0.56.
- **FastAPI + SSE dashboard** that attaches to the live bot's audit ledger
  when present and runs a seeded mock session otherwise — zero API keys.
- **The honesty layer**: an append-only paper track record, one row per
  day, and a backtest engine that publishes losing regimes next to winning
  ones.

The worst code in the repo is the part that predicted gold's direction —
and that's the point. https://github.com/muhammadwhizz-web/gold-reaper

## Draft 3 — Hacker News (Show HN)

**Title:** Show HN: Gold Reaper – an honest quant bot that publishes its losses

**Body:**

Hi HN. Most open-source trading bots show you their one lucky backtest.
I built the opposite: a gold (XAU/USD) system whose README leads with the
walk-forward results where it loses (-970 USD, PF 0.66), shows exactly
which regimes, hours and modules bleed, and then ships the fix only after
it passes a worst-fold walk-forward gate.

Stack: Python, pandas, DuckDB (feature store), hmmlearn (regime router),
XGBoost (soft vote only — the hard ML gate never cleared OOS AUC 0.56),
ccxt + MetaTrader 5 for execution, FastAPI for the ops dashboard.

The part I care most about: an append-only paper track record. The backtest
says "maybe a small edge" on 8-11 trades, which is nothing. The paper ledger
is what arbitrates that claim, in public, one row per day.

Install is one command on Linux/macOS or a double-click .exe on Windows.
Paper mode is the default and the circuit breakers latch on -3% day.
MIT, reproducible with one command: https://github.com/muhammadwhizz-web/gold-reaper

Ask me anything about the walk-forward methodology or the ML rejection.

## Draft 4 — X / Twitter thread

**Tweet 1:**
I built an autonomous gold trading bot and published the part everyone hides: the walk-forward results where it loses.

-970 USD. PF 0.66. Worst fold -663.

Then I diagnosed it like a system, not a lottery ticket. 🧵

**Tweet 2:**
The diagnosis bucketed every trade:

· RANGE-regime mean reversion: -526 (fading a trending tape)
· hours 14-15 UTC: -1,010 combined
· SL-first vs TP-first fills: identical to the cent
· even zero-cost execution loses

Costs weren't the story. The entries were.

**Tweet 3:**
Three fixes survived the only gate I trust: the WORST walk-forward fold must improve.

· hunt 12-14 UTC only
· TP 2.67R → 2.0R
· meanrev gated to low-vol tape

Worst fold: -663 → -112. Full period: -970 → +34.

On 11 trades. An anecdote, not a sample.

**Tweet 4:**
So the arbitration is public: an append-only paper track record, one row per UTC day, win or lose.

If the edge is fake, the ledger says so in the open.

Every number reproducible with one command. MIT. Paper mode default.
github.com/muhammadwhizz-web/gold-reaper

## Draft 5 — LinkedIn

**Title:** What publishing your model's failures teaches you (gold trading bot, walk-forward diagnosis)

I spent the last stretch of this project doing the least glamorous work in
quant development: publishing the losses.

My open-source gold trading bot's walk-forward backtest lost money (-970 USD,
profit factor 0.66). Instead of iterating in private until a lucky config
appeared, I shipped the diagnosis:

- regime buckets, hour buckets, weekday buckets, module attribution
- hypothesis tests: fill assumptions (not the problem), cost sensitivity
  (real but not the root cause), longer-window ML (0.528 AUC — rejected)
- a promotion rule that only accepts a config change if the WORST fold
  improves — the anti-overfitting discipline that matters more than any
  single metric

The hardened config cut the worst fold from -663 to -112 and turned the
full period slightly positive (+34 USD). On 8-11 trades. Which is why the
next layer is an append-only paper track record: one row per day, in
public, because a backtest on a tiny sample proves nothing.

The broader lesson for anyone shipping ML/data products: the discipline of
publishing what DOESN'T work is what makes the eventual positive result
believable.

Repo (MIT, one-command install, paper mode default):
https://github.com/muhammadwhizz-web/gold-reaper

## Rules of engagement (from docs/BRAND.md)

- Never claim profits. Quote the reproducible numbers, including the folds
  that lose.
- No hype words. Quiet, technical, confident.
- Every reply to "so does it make money?" gets the same honest answer:
  "the paper ledger is the live answer; the backtest says the sample is too
  small to know yet."
