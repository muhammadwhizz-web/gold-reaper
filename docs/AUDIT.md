# GOLD//REAPER — RELIABILITY AUDIT (fix/reliability-v1)

Baseline audited: `baa0cce1c3a148c311a05d3f961bfb837443489a` (main @ v3.2.0).
Every defect below was verified against the actual code, not assumed. Each
fix lands with a test; see docs/REPAIR_REPORT.md for results.

Severity: **P0** = silently wrong money behavior · **P1** = wrong/unsafe
operations behavior · **P2** = hygiene that lets the above hide.

---

## P0 — system is broken until these are fixed

### P0-A — Paper trades never close on `EXIT_SL` / `EXIT_TP`
- **Files**: `bot.py` (`_manage_positions`), `brokers/paper_broker.py`
- **Root cause**: `core/strategy.py::manage_exit()` (line 170–180) returns
  `("EXIT_SL", sl)` / `("EXIT_TP", tp)` when a bar crosses the stop or
  target, but `bot.py::_manage_positions` only handles `action in ("BE",
  "TRAIL")` (line 602). The exit actions are dropped on the floor. There is
  no second line of defense: `PaperBroker` never looks at SL/TP on price
  ticks either. A paper position that hits its stop **stays open forever**,
  bleeding unrealized PnL into the equity curve and never realizing it.
- **Evidence**: grep — `close_position` is called nowhere in `bot.py`.
- **Fix**:
  1. `bot.py` handles `EXIT_SL` → `broker.close_position(ticket,
     reason="SL", intended_price=sl_price)` and `EXIT_TP` →
     `close_position(ticket, reason="TP", intended_price=tp_price)`.
  2. `PaperBroker` gains an **independent safety net**: every time fresh
     candle data arrives (and on manual `enforce_stops()` calls), any open
     position whose bar crossed SL/TP is force-closed — even if the
     strategy loop never acted. Conservative same-candle rule: if a bar's
     high ≥ TP **and** low ≤ SL, assume **SL hit first**
     (`EXIT_SL_CONSERVATIVE`).
  3. Exactly-once close: per-position `closed` guard + dedupe of closed
     trades; close records exit reason, exit price, timestamp, realized
     net PnL.
- **Tests**: `tests/test_exit_execution.py` (SL cross, TP cross, both-hit →
  SL first, SL-only touch, double `manage_exit` → exactly one close,
  safety-net closes without the strategy loop).

### P0-B — Paper account state evaporates on restart
- **Files**: `brokers/paper_broker.py`
- **Root cause**: `balance_val` and `positions` live only in memory
  (`__init__` resets to `cfg.starting_balance`). Restart = account wiped,
  open positions vanished, realized history gone. Worse, the risk layer
  keeps its own balance ledger, so equity drifts apart from reality.
- **Fix**: `data/paper_account.json` persisted on **every** state change
  (order open, close, SL modify, 60 s heartbeat, SIGTERM/atexit):
  starting balance, cash balance, full open-position detail, append-only
  closed trades, fees paid, realized PnL, running equity, schema version.
  Atomic write: tmpfile → `fsync` → `os.replace`. Load rules:
  missing → fresh start (logged); unknown schema version → **refuse to
  start**, print exact path, never overwrite; corrupt JSON → back up to
  `paper_account.json.bak`, **refuse to trade** until the user confirms.
  Best-effort mirror to DuckDB (`paper_account`, `paper_trades`).
- **Tests**: `tests/test_persistence.py` (open → restart → position and
  balance intact; close → restart → realized PnL intact; corrupt file →
  refuse + `.bak`; crash mid-write → old state intact).

### P0-C — Risk systems keep two competing ledgers
- **Files**: `core/risk.py` + `core/risk_apex.py` (both **protected**),
  `bot.py` (wiring)
- **Root cause**: `RiskManager` (data/state.json) and `ApexRisk`
  (data/apex_risk.json) each track balance/equity/day-PnL/streak
  independently. `bot.py` registers fills **only** into `ApexRisk`
  (line 621) — `RiskManager.state.day.realized_pnl` and its consecutive-loss
  counter stay at zero forever, so its daily-loss breaker can never trip.
  After a restart the two ledgers disagree.
- **Fix (within the freeze)**: new `core/account_state.py` — a single
  canonical `AccountState` (balance, equity, realized PnL, day PnL, streak,
  fees) persisted atomically to `data/account_state.json`. `bot.py` owns
  the instance and hydrates **both** risk managers from it on start and
  after every fill/equity sync (attribute-level injection — the protected
  risk modules are not edited). Fill registration now goes to both
  managers from the same event.
- **Tests**: `tests/test_risk_consistency.py` (identical trade stream →
  identical risk decisions; restart mid-day → identical limits; daily-loss
  breaker latches exactly once).
- **Protected-file defect note**: the *formulas* inside the two risk
  modules differ (legacy daily-loss uses equity %, APEX uses day-PnL vs
  day-start-equity with latch). That divergence is a risk-design decision
  that lives inside protected files → recorded in
  docs/STRATEGY_DEFECTS.md, not "fixed" here.

---

## P1 — operations must not lie

### P1-A — `PaperBroker.connect()` validates nothing
- **File**: `brokers/paper_broker.py`
- **Root cause**: `connect()` prints a banner and returns `True`
  unconditionally. A dead/stale/empty Yahoo feed still reports "connected"
  and the bot happily simulates against zero data. The feed is **Yahoo
  `GC=F` (CME gold futures), not XAU/USD spot** — nowhere documented in
  the adapter.
- **Fix**: `connect()` fetches the last 3 daily bars + recent hourly bars
  and asserts: present, finite, positive, last bar ≤ 24 h old (weekend
  grace when the gold market is scheduled-closed — documented), ≥ 20
  recent hourly bars for indicators. Any failure → `ConnectionStatus.
  DEGRADED` with a precise reason; `connect()` returns False. Instrument
  header + `PAPER_INSTRUMENT=GC=F` in `.env.example`. No fake prices, ever.
- **Tests**: `tests/test_feed_validation.py` (empty feed, stale bars, NaN
  prices → DEGRADED; valid bars → CONNECTED).

### P1-B — Failover silently swaps live ↔ paper
- **Files**: `brokers/factory.py`, `bot.py`
- **Root cause**: `connect_with_failover()` walks MT5 → BITGET → PAPER and
  guarantees a PaperBroker ("the bot never dies"); `normalize()` silently
  maps *any* garbage broker name to PAPER. A user who requests MT5 with a
  bad password gets a **paper bot that looks live**. Live order failures
  can also surface as paper success in dashboards.
- **Fix**: `requested_broker` (from env/CLI, normalized) vs `active_broker`
  (actually connected) tracked explicitly. A failed live primary **halts
  the bot** (`BrokerHaltError` → exit code 3, supervisor does not restart
  code 3); paper fallback for a live request is forbidden — even with
  `ALLOW_PAPER_FALLBACK=true` (that flag only governs paper-requested
  runs). Mismatch (when reachable) → WARNING log + boxed dashboard banner
  + Telegram alert. Dashboard heartbeat now carries
  `requested_broker`/`active_broker`/`broker_label`
  ("BROKER: MT5 (LIVE)" / "BROKER: PAPER (fallback from MT5)").
- **Tests**: `tests/test_failover.py` (MT5 fails → halt code 3, no paper
  order; MT5 fails + ALLOW_PAPER_FALLBACK=true → still halts; PAPER fails
  → halts; requested == active → no warning).

### P1-C — Config silently swallows bad values
- **File**: `core/config.py` (**protected**) → realized in new
  `core/config_validation.py`
- **Root cause**: `_f()`/`_i()` wrap `os.getenv` in `except:
  return default`. `RISK_PER_TRADE_PCT=abc` silently becomes 1.0;
  `RISK_PER_TRADE_PCT=10` sails through; `BROKER=FIDELITY` silently
  becomes paper. The bot then runs with numbers the user never chose.
- **Fix (within the freeze)**: `core/config_validation.py` re-reads the
  raw env strings and validates: numeric risk values, `risk ≤ 3%`,
  daily-loss stop in (−10 %, 0 %], session target > 0, broker name ∈
  known aliases. Invalid → `ConfigError` naming the key and the bad value
  → bot refuses to start. Wired into `bot.main()` and the health check.
  Frozen-file note: the silent-fallback code inside `config.py` itself is
  documented in docs/STRATEGY_DEFECTS.md.
- **Tests**: `tests/test_config_validation.py` (`risk=abc` → fail with
  message; `risk=10` → fail; bad daily stop; bad broker; valid config →
  loads).

### P1-D — Dashboard binds `0.0.0.0` with no auth, and mock data looks real
- **Files**: `dashboard/app.py` (:8080 console), `core/dashboard.py`
  (:8050 bot console), `bot.py` (spawns :8050 on 0.0.0.0)
- **Root cause**: both dashboards `uvicorn.run(host="0.0.0.0")` — the
  kill-switch endpoint `POST /api/standby` is exposed to the LAN by
  default. No auth. In mock (no-bot) mode the console renders a plausible
  equity curve and position with **no unavoidable "not real data"
  banner**.
- **Fix**: bind `127.0.0.1` by default; `DASHBOARD_BIND=0.0.0.0` requires
  `DASHBOARD_USER`/`DASHBOARD_PASS` (refuse to start otherwise). Optional
  HTTP-basic auth on all routes when creds are set. Mock mode → injected
  red banner `⚠ DEMO MODE — NOT REAL DATA` in the served HTML. Broker
  rows carry requested-vs-active. `core/dashboard.py` + bot spawn path get
  the same bind rules.
- **Tests**: `tests/test_dashboard_mode.py` (mock → banner present in HTML;
  live → no banner + mode live; bind helper refuses 0.0.0.0 without
  creds; auth challenges when configured).

### P1-E — Installers are best-effort, not gated
- **Files**: `install_linux.sh`, `install_macos.sh`, `install_windows.ps1`,
  `start.sh`, `start_windows.bat`, `start.command`
- **Root cause (audit)**: installers install whatever pip resolves, never
  run a health gate, and `start.*` exit with a confusing error when
  `.venv` is missing. A broken data-ingest still prints success.
- **Fix**: rewritten per spec — OS+arch detection; Python 3.10–3.12
  resolution chains (Linux: pyenv → uv → standalone → fail with link;
  macOS: `brew install python@3.12` + validate; Windows: `py -3.12` →
  winget with **explicit `$LASTEXITCODE` check** → validate); `.venv/` at
  project root; pinned `requirements.lock` (hashes) install; **Phase-4
  health check gate** — SUCCESS only when it passes; failed ingest =
  failed install; idempotent (stops old services first); never overwrite
  an existing `.env`; service registration with auto-restart (systemd
  user unit / Task Scheduler / LaunchAgent); desktop icon on all three
  OSes; `start.*` self-heal by invoking the installer when `.venv` is
  missing.
- **Tests**: `.github/workflows/installer-e2e.yml` (install + health +
  uninstall on ubuntu-22.04/24.04, macos-13/14, windows-2022) — plus the
  health check itself exercised in CI.

---

## P2 — hygiene

1. **Dependency split**: `requirements-paper.txt` (minimal paper-mode
   stack), `requirements-live.txt` (MT5/Bitget extras), and
   `requirements.lock` generated with hashes (`uv pip compile
   --generate-hashes`). `requirements.txt` stays as the full stack for
   backward compatibility.
2. **CI**: `pytest -v` runs real tests on every push across the matrix;
   failures block merge (branch protection note for the owner).
3. **docs/PARAMETERS.md**: single source of truth for every tuned number
   (SL_ATR_MULT, TP_R, risk %, hunt window…); README/docstrings reference
   it instead of duplicating. Protected files are not edited to add
   docstring links (freeze); the mapping table documents every parameter
   and its consuming module.

---

## P2 defects found during audit (recorded, fixed here where cheap)

- `bot.py` imports `json` at line 198 **after** using it (`_write_heartbeat`
  is defined earlier but only *called* later — latent, now harmless; import
  consolidated to the top).
- `PaperBroker.close_position` ignored its `reason` argument and priced
  exits off `last_price()` — replaced by intended-price fills (P0-A).
- `connect_with_failover` retried a broken chain and still returned paper
  — replaced by explicit halt (P1-B).
- `data/paper_account.json`, `data/account_state.json` added to
  `.gitignore` (runtime state must never be committed).
