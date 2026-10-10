# GOLD//REAPER — PARAMETERS (single source of truth)

Every number the bot lives and dies by, in one table. README, docstrings
and docs reference **this file** — they do not restate values. The runtime
source is `.env` → `core/config.py` (frozen) → validated by
`core/config_validation.py` (refuses invalid values at startup).

Change protocol: any edit to a **strategy-tuned** row invalidates the
walk-forward claim and requires a fresh `research/optimize.py` run before
merge to main. **Safety** rows may be tightened freely, loosened only with
a written justification.

## Broker & capital

| Parameter | Env | Default | Constraint (P1-C) | Notes |
|---|---|---|---|---|
| Starting balance | `STARTING_BALANCE` | `1000` | `> 0` | paper + risk math base |
| Broker | `BROKER` | `PAPER` | ∈ {MT5, BITGET, PAPER} | aliases: EXNESS→MT5, DEMO/TEST→PAPER; unknown = refuse to start |
| Paper mode | `PAPER_MODE` | `true` | — | always default to paper |
| Paper instrument | `PAPER_INSTRUMENT` | `GC=F` | — | **CME gold futures via Yahoo — NOT XAU/USD spot** (P1-A honesty) |

## Risk (safety rows)

| Parameter | Env | Default | Constraint | Notes |
|---|---|---|---|---|
| Risk per trade | `RISK_PER_TRADE_PCT` | `1.0` % | `(0, 3]` | hard ceiling 3% at validation; APEX adaptively clamps 0.5–2% (`core/risk_apex.py`) |
| Max daily loss | `MAX_DAILY_LOSS_PCT` | `3.0` % | magnitude `(0, 10]` | legacy gate; APEX latches at −3% day / −7% week / −15% month (frozen) |
| Max consecutive losses | `MAX_CONSEC_LOSSES` | `3` | `≥ 1` | enforced by BOTH risk layers |
| Max open trades | `MAX_OPEN_TRADES` | `1` | `≥ 1` | |
| Max trades / session | `MAX_TRADES_PER_SESSION` | `4` | `≥ 1` | legacy session cap |

## Mission targets

| Parameter | Env | Default | Constraint | Notes |
|---|---|---|---|---|
| Block target | `TARGET_PER_SESSION_USD` | `20` | `> 0` | per 4h block (`BLOCK_TARGET`, frozen constant) |
| Day target | `TARGET_PER_DAY_USD` | `120` | `> 0` | informational |
| Stop after target | `STOP_AFTER_TARGET` | `true` | — | |

## Strategy geometry (strategy-tuned — frozen by the repair)

| Parameter | Env | Default | Constraint | Consumed by |
|---|---|---|---|---|
| H4 bias EMAs | `HTF_EMA_FAST` / `HTF_EMA_SLOW` | `50` / `200` | — | `core/strategy.py::compute_bias` |
| Pullback EMA | `EMA_PULLBACK` | `20` | — | entry reset band |
| RSI period | `RSI_PERIOD` | `14` | — | reset zones (longs 38–52, shorts 48–62, frozen tuples) |
| ATR period | `ATR_PERIOD` | `14` | — | everywhere |
| Min ADX | `MIN_ADX` | `24.0` | — | trend-strength gate (walk-forward v2.3) |
| **SL distance** | `SL_ATR_MULT` | `1.2` ×ATR | `> 0` | `core/strategy.py::evaluate` — **walk-forward selected** |
| **TP distance** | `TP_R` | `2.0` R | `> 0` | TP = entry ± `TP_R × SL_ATR_MULT × ATR` — **walk-forward selected** |
| Legacy TP alias | `TP_ATR_MULT` | `2.4` | — | kept for compat; `TP_R` wins where both are read |
| Hunt hours | `ENTRY_HOURS_UTC` | `12,13` | — | hardened window (14–15 UTC bleeds) |
| Blackouts | (code constant) | `12:25/12:35/13:25/13:35` UTC | — | US data bombs, `core/sessions.py::in_blackout` |
| Mean-rev vol ceiling | `MEANREV_VOL_MAX` | `0.4` | — | APEX meanrev module |
| Breakeven trigger | `BREAKEVEN_AT_R` | `1.0` R | — | `manage_exit` (frozen) |
| Trail start | `TRAIL_START_R` | `1.5` R | — | `manage_exit` (frozen) |
| Trail distance | `TRAIL_ATR_MULT` | `1.2` ×ATR | — | `manage_exit` (frozen) |
| Dead-tape floor | `MIN_ATR_PRICE` | `3.0` $ | — | skip still markets |

## Sessions (UTC)

| Parameter | Env | Default | Notes |
|---|---|---|---|
| Asia | `TRADE_ASIA` | `false` | recon: negative expectancy |
| London | `TRADE_LONDON` | `false` | walk-forward: −$2,636 — stays off |
| **Overlap** | `TRADE_OVERLAP` | `true` | 12:00–16:00 UTC, the only hunted window |
| New York | `TRADE_NEWYORK` | `false` | |
| Friday last entry | `FRIDAY_LAST_ENTRY_UTC` | `18` | weekend guard |
| Pre-close blackout | (code constant) | `120` min | before Fri 21:00 UTC close |

## Runtime & ops

| Parameter | Env | Default | Constraint | Notes |
|---|---|---|---|---|
| Poll interval | `POLL_SECONDS` | `60` | `≥ 5` | feed is hourly; hammering gets rate-limited |
| Heartbeat | `HEARTBEAT_MINUTES` | `30` | — | console heartbeat lines |
| Failover chain | — | removed | — | **P1-B**: chain = requested broker only; live failure = halt (exit 3), never silent paper swap |
| Paper fallback flag | `ALLOW_PAPER_FALLBACK` | unset | — | can NEVER authorize live→paper (tested) |
| Dashboard bind | `DASHBOARD_BIND` | `127.0.0.1` | remote bind requires auth | P1-D |
| Dashboard auth | `DASHBOARD_USER` / `DASHBOARD_PASS` | unset | both required for remote bind | HTTP basic on all routes |
| Supervisor | `SUPERVISOR` | `1` | — | exit code 3 is never restarted |
| Paper state flush | (code constant) | `60 s` | — | P0-B heartbeat save |

## Paper execution model (P0-A)

| Constant | Value | Notes |
|---|---|---|
| `SPREAD` | `$0.35` /oz | paid inside the entry fill price |
| `SLIPPAGE` (exit fee) | `$0.05` /oz | charged on every close; PnL is net |
| SL fill | exactly at the stop price | no gap-through modeling (SD-5) |
| Same-candle both-hit | **SL first** | conservative; reason `EXIT_SL_CONSERVATIVE` (mirrors frozen `manage_exit`) |
| Close guard | exactly once per ticket | duplicate closes return `None` and are logged |
