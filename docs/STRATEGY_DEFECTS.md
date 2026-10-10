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
