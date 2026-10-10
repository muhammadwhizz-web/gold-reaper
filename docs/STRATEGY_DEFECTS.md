# GOLD//REAPER — STRATEGY DEFECT REGISTER (out of repair scope)

The repair branch `fix/reliability-v1` freezes every module that influences
signal generation, entry, exit geometry, sizing, or ensemble voting
(see docs/PROTECTED_HASHES.txt). Defects found **inside** those protected
files during the audit are recorded here — visible, honest, and explicitly
NOT fixed on the repair branch. Fixing any of them changes trading
behavior and requires its own walk-forward revalidation.

## SD-1 — Silent config fallback (`core/config.py`)
`_f()` / `_i()` swallow `ValueError` and return the default. Invalid env
values run silently under tuned defaults instead of refusing to start.
**Repair-branch mitigation**: `core/config_validation.py` re-validates the
raw env and refuses to start on bad values (P1-C). The silent code inside
`config.py` remains frozen.

## SD-2 — Two daily-loss formulas (`core/risk.py` vs `core/risk_apex.py`)
`RiskManager.can_trade()` trips at `day.realized_pnl <= -pct × equity`;
`ApexRisk._check_breakers()` trips at `day_pnl / day_start_equity <= -3%`
and **latches** (manual reset). Same event, two definitions, two thresholds.
The repair branch unifies the *inputs* (single AccountState hydrated into
both) but not the formulas. Chosen intentionally: which formula is
"correct" is a risk-policy decision.

## SD-3 — Legacy `RiskManager` never saw fills (wiring, not logic)
`bot.py` registered fills only into `ApexRisk`, so the legacy manager's
day counters and streak were permanently zero. The wiring is fixed in
`bot.py` (unprotected); the legacy manager's own session/day model stays
as-is inside the frozen file.

## SD-4 — `manage_exit()` conservative-order assumption
`core/strategy.py::manage_exit()` checks SL **before** TP on every bar
(conservative). The paper safety net mirrors this rule so both paths agree
(`EXIT_SL_CONSERVATIVE` on same-candle both-hit). If the strategy's rule
ever changes, the safety-net rule must change with it — cross-referenced
in both docstrings.

## SD-5 — `EXIT_SL`/`EXIT_TP` exit prices assume exact fills
The strategy returns the SL/TP *intended* price; the paper broker fills
SL exactly at the stop (no gap-through modeling) and charges an exit fee.
Real stops can gap beyond the stop level. Modeling gaps is a strategy-
fidelity decision, out of scope for the repair.

## SD-6 — Breaker re-latches (and re-alerts) on every breaching fill (`core/risk_apex.py`)
`ApexRisk.register_fill()` calls `_check_breakers()` unconditionally, and
`_check_breakers()` calls `_latch()` every time the day/week/month loss is
past the stop — even when the breaker is ALREADY latched. Every breaching
fill rewrites the breaker file and re-fires the notification (alert spam,
shifting latch reason). The *gate* path (`can_open`) is correctly silent
once latched. Fix requires editing the frozen module → out of repair
scope. The repair-branch test pins the gate-path once-only contract.



---
Any change to the protected files must: (1) update
docs/PROTECTED_HASHES.txt deliberately, (2) justify the change here,
(3) rerun the full walk-forward before merge to main.

## SD-7 — Post-breakeven `r_dist` uses the orig_sl PRICE, not the DISTANCE (`core/strategy.py`, discovered during the HPE build 2026-10-10)
`ReaperX.manage_exit()` computes
`r_dist = abs(entry - sl if not be_moved else pos["orig_sl"])`. Once the
stop is at breakeven, `pos["orig_sl"]` is the ORIGINAL SL *price*
(bot.py passes `pos.orig_sl or pos.sl`, e.g. 3348.8) — so `r_dist`
becomes ~3348 instead of the risk distance (~1.2 x ATR). The R-multiple
is deflated ~2800x, `r_mult >= trail_start_r` can never fire, and the
trailing stop is dead for the rest of the trade (only the breakeven stop
protects it). `research/backtest_apex.py` uses the correct
`abs(entry - orig_sl)` math, so backtests show a live trailing behavior
that live trading never had. Frozen, NOT fixed here. HPE implements the
correct distance in `core/strategy_hpe.py` with a regression test
(`tests/test_strategy_hpe.py::test_r_dist_uses_orig_sl_distance_not_price`).
Live impact while frozen: REAPER-X/APEX-X winners that reach +1R are
protected at breakeven but never trail; realized R on trailed winners is
backtest-optimistic.


## SD-8 — NaN ATR pass-through in `ApexX.evaluate` (`core/strategy_apex.py`, discovered 2026-10-10 audit)
`atr_v = f_atr_14_norm * close` is NaN when the feature row is NaN; both
guards (`atr_v <= 0`, `atr_v < min_atr_price`) are False for NaN, so a
signal can be built with `sl = entry ± 1.2×NaN`, poisoning risk sizing.
Frozen, NOT fixed here. Mitigation upstream: feature rows are
NaN-dropped before evaluate in the live path; the audit recommends an
`isfinite` guard if the freeze is ever lifted.

## SD-9 — `ApexRisk.__init__` save-before-load wipes persisted state (`core/risk_apex.py`, discovered 2026-10-10 audit)
The constructor calls `sync_period_starts()` which ends in `save()` —
BEFORE `bot.start()` calls `load()`. Every restart overwrote
`data/apex_risk.json` with defaults: `week_pnl`, `month_pnl`, block
state, recovery mode and `risk_multiplier` were lost (weekly/monthly
breakers had zero memory across restarts; crash-loops voided
cumulative-loss protection). **Mitigated 2026-10-10 WITHOUT touching the
frozen file**: `bot.py` snapshots the file before construction and
restores it after (`_apex_state_stash/_apex_state_restore`), so
`load()` re-reads the real history. Regression test:
`tests/test_reliability_v2.py::TestApexStateStash`.

## SD-10 — corrupt `breakers.json` / `apex_risk.json` / `state.json` fail OPEN (`core/risk_apex.py`, `core/risk.py`, discovered 2026-10-10 audit)
`is_latched()` and both `load()`s swallow parse errors and return
defaults — the most safety-critical files fail open. **Mitigated at the
bot boundary (frozen files untouched)**: `bot._guard_runtime_state()`
parses all three files at startup and refuses to start (exit 3) on
corruption. Regression test:
`tests/test_reliability_v2.py::TestGuardRuntimeState`.

## SD-11 — `--reset-breakers` re-latches instantly (`core/risk_apex.py`, discovered 2026-10-10 audit)
`reset_breakers()` only unlinks the breaker file; `day_pnl` still
breaches, so the next `can_open()` re-latches (and re-notifies). The
escape hatch cannot work within the breaching period. NOT fixed
(frozen): a real fix requires a policy decision about zeroing the
breaching bucket in the money truth. Conservative direction preserved:
the bot keeps refusing entries, never trades through a broken stop.

## SD-12 — `RiskManager.load()` silently resets on corrupt file; non-atomic save (`core/risk.py`, discovered 2026-10-10 audit)
`load()` except-pass → fresh `DayStats`; `save()` is a plain
`write_text` (no tmpfile/replace). **Startup corruption is mitigated by
the SD-10 bot-level guard; mid-run torn writes remain** (window is one
small JSON write per fill/equity sync).

## SD-13 — `PAPER_MODE` was a dead gate (`core/config.py` wiring, discovered 2026-10-10 audit)
`cfg.paper` was assigned in four places and read in ZERO: `BROKER=MT5`
with the .env-template default `PAPER_MODE=true` connected a LIVE
account while the user believed paper mode was active (README claimed
otherwise). **Mitigated in `core/config_validation.py`** (unfrozen):
live BROKER now requires explicit `PAPER_MODE=false`, else exit 2.
Regression tests: `tests/test_config_validation.py::TestPaperModeLiveGate`.

## SD-14 — HMM regime engine never trained (`core/regime.py`, discovered 2026-10-10 audit; FIXED 2026-10-10)
`Xs.values[:-0]` was an EMPTY slice (`-0 == 0`) — `GaussianHMM.fit`
always raised, was silently swallowed, and every environment ran the
rule fallback while docs/banners claimed the HMM was primary. The
CRISIS coverage fallback also used `vol_rank.idxmax()` (the LEAST
volatile state). **Both fixed (regime.py is NOT a protected file)**;
`fit()` now trains and `classify()` may return HMM labels where it
previously always returned rule labels. Backtests are unaffected
(`classify_frame()` is a separate rules-only path).

## SD-15 — live vs backtest regime precedence diverge (`core/regime.py`, discovered 2026-10-10 audit)
For `vr∈(1.6,2.2]` with ADX>25 and |edist|>0.004, live `classify()`
returns TREND_* while backtest `classify_frame()` returns
VOLATILE_CHOP. Regime-scaled sizing/gates therefore differ between
research and production. NOT fixed — unifying changes trading behavior
and requires its own walk-forward comparison (Phase-10 experiment
candidate). Documented so no one trusts cross-path regime comparisons
blindly.
