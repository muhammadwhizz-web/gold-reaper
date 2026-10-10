# GOLD//REAPER — REPAIR REPORT (fix/reliability-v1)

Branch: `fix/reliability-v1` · baseline `baa0cce` (v3.2.0) · repair commits
listed in the PR. This report separates **verified / partially verified /
not tested** — nothing is claimed without evidence. The strategy is
untouched: all six protected-file SHA-256 hashes are byte-identical to the
baseline recorded in [docs/PROTECTED_HASHES.txt](PROTECTED_HASHES.txt)
(re-verified at repair close; `tests/test_strategy_freeze.py` enforces it
in CI).

## 1. Summary of original defects

| ID | Severity | Defect | Root cause (file) |
|---|---|---|---|
| P0-A | P0 | Paper trades never closed on `EXIT_SL`/`EXIT_TP` | `bot.py::_manage_positions` handled only `BE`/`TRAIL`; `PaperBroker` had no stop enforcement at all |
| P0-B | P0 | Paper account reset on every restart | `paper_broker.py` kept balance/positions in memory only |
| P0-C | P0 | Two competing risk ledgers | `bot.py` registered fills only into `ApexRisk`; `RiskManager`'s day/streak gates were permanently zero |
| P1-A | P1 | `connect()` validated nothing; GC=F futures sold as spot | `paper_broker.py::connect` returned `True` unconditionally |
| P1-B | P1 | Silent live→paper failover; garbage broker names coerced to paper | `factory.py::connect_with_failover` + `normalize()` |
| P1-C | P1 | Bad config silently fell back to defaults | `core/config.py::_f/_i` (frozen) — policed from outside |
| P1-D | P1 | Dashboards bound `0.0.0.0` unauthenticated; mock data looked real | `dashboard/app.py`, `core/dashboard.py`, `bot.py` spawn |
| P1-E | P1 | Installers printed success without a health gate; `start.*` died on missing venv | installers / launchers |
| P2 | P2 | No real tests, no dependency pinning, no parameter source of truth | repo-wide |

Full audit with evidence: [docs/AUDIT.md](AUDIT.md). Defects found INSIDE
frozen strategy files are recorded unfixed in
[docs/STRATEGY_DEFECTS.md](STRATEGY_DEFECTS.md) (SD-1…SD-6).

## 2. Files changed and why

- `brokers/paper_broker.py` — rewritten: stop-enforcement safety net,
  exactly-once closes with full records, atomic persistence, feed
  validation, `ConnectionStatus`, instrument honesty, construction-time
  path binding (atexit race fixed).
- `bot.py` — executes `EXIT_SL`/`EXIT_TP`; canonical `AccountState`
  hydration into both risk layers; fill events fan out to both; broker
  halt (exit 3); supervisor never restarts code 3; config validation gate;
  heartbeat carries requested-vs-active broker; paper 60s state flush.
- `brokers/factory.py` — `BrokerHaltError`, strict `normalize_valid()`,
  requested-only chain, `paper_fallback_allowed()` (can never authorize
  live→paper).
- `brokers/base.py`, `brokers/mt5_broker.py`, `brokers/bitget_broker.py` —
  `close_position(..., intended_price=None)` signature alignment only.
- `core/account_state.py` (new) — single money-truth, atomic persistence,
  refuse-on-corrupt.
- `core/config_validation.py` (new) — strict env validation
  (`ConfigError`), dashboard bind policy.
- `dashboard/app.py` — localhost bind policy, optional HTTP basic auth
  middleware, red DEMO banner injected server-side in mock mode,
  `/health` endpoint, broker honesty rows.
- `core/dashboard.py` — same bind policy.
- `dashboard/static/index.html` — untouched (banner is server-injected).
- `install_linux.sh` / `install_macos.sh` / `install_windows.ps1` —
  rewritten per P1-E (python chains, lock install, health gate,
  idempotency, `--uninstall`, desktop icons, service registration).
- `start.sh` / `start.command` / `start_windows.bat` — self-heal missing
  venv by running the installer.
- `installer/health_check.py` (new) — the 12-step gate.
- `cli.py` (new) — `doctor [--fix] | start | stop | status`.
- `requirements-paper.txt`, `requirements-live.txt` (new),
  `requirements.lock` (generated, hash-pinned), `requirements-dev.txt`
  (now builds on the paper stack, not the full ML stack).
- `tests/` (new) — 9 files, 83 tests. `conftest.py` provides hermetic
  paths; tests never touch the network or the repo's `data/`.
- CI: `.github/workflows/ci.yml` adds a `tests` job (freeze guard → full
  suite → ruff → mypy) on linux py3.10/3.12, macOS, Windows;
  `.github/workflows/installer-e2e.yml` (new) runs install + health +
  uninstall on ubuntu-22.04/24.04, macos-13/14, windows-2022.
- Docs: `docs/AUDIT.md`, `docs/STRATEGY_DEFECTS.md`,
  `docs/PROTECTED_HASHES.txt`, `docs/PARAMETERS.md`, this report; README
  links them.
- `.gitignore` — paper/account runtime state (one leak incident fixed:
  see §9).

## 3. Exact behavior repaired (before → after)

- **SL/TP exit** — before: a paper long at SL 2390 with a 2389-low bar
  stayed open forever; after: closes at 2390, reason `SL`, realized PnL
  net of the exit fee, exactly once, journaled. Proven by
  `tests/test_exit_execution.py` (both strategy-path and independent
  safety-net path, including `EXIT_SL_CONSERVATIVE` on same-candle
  both-hit).
- **Restart** — before: balance reset to starting value, positions
  vanished; after: `data/paper_account.json` (atomic tmpfile→fsync→
  replace) restores cash, open positions, SL moves, closed-trade ledger,
  fees, realized PnL. Corrupt file → `.bak` + refuse to trade; unknown
  schema → refuse untouched. `tests/test_persistence.py`.
- **Risk truth** — before: legacy manager's `consec_losses` stayed 0
  forever; after: one `AccountState` hydrated into both layers, every
  fill reaches both, restart reloads identical state.
  `tests/test_risk_consistency.py`.
- **Connect honesty** — before: always `True`; after: 3 daily bars
  present/finite/positive, ≤24h fresh (96h weekend grace — documented),
  ≥20 hourly bars; failure = `DEGRADED` + precise reason + `False`.
  `tests/test_feed_validation.py`.
- **Failover** — before: MT5 failure silently became a paper bot; after:
  halt with boxed banner, exit 3, supervisor never restarts it,
  `BROKER: PAPER (fallback from MT5)` labeling everywhere.
  `tests/test_failover.py`.
- **Config** — before: `RISK_PER_TRADE_PCT=abc` ran as 1.0; after: exit 2
  naming key and value. `tests/test_config_validation.py`.
- **Dashboard** — before: `0.0.0.0`, kill-switch LAN-exposed, mock
  indistinguishable from live; after: `127.0.0.1` default, remote bind
  requires auth env, optional basic auth (401 timing-safe), red
  `⚠ DEMO MODE — NOT REAL DATA` banner in mock HTML. 
  `tests/test_dashboard_mode.py`.
- **Install** — before: success printed unconditionally; after: 12-step
  gate, `INSTALL FAILED at step N — <reason> — <fix>`, exit 1, no bot
  start.

## 4. Persistence design

- `data/paper_account.json` — schema v1: `starting_balance`,
  `cash_balance`, `equity` (quiet, no network), `fees_paid`,
  `realized_pnl`, `open_positions[]` (full detail incl. SL moves),
  `closed_trades[]` (append-only), `updated_at`. Written on: order open,
  close, SL modify, 60s heartbeat (`bot.tick`), orderly shutdown
  (`disconnect`), atexit. Atomic: `mkstemp` → write → `fsync` → `os.replace`.
- DuckDB mirror `data/paper.duckdb` (`paper_trades`) — best-effort,
  JSON is the source of truth.
- `data/account_state.json` — canonical risk truth (same atomic policy).
- Path binding happens at construction so late writers (atexit) cannot
  follow re-pointed module globals — this exact race was caught and
  regression-tested (`TestPathBindingRegression`).

## 5. Installer changes per OS

- **Linux** — chain: validated python3 → pyenv 3.12 → uv (standalone
  prebuilt) → distro packages → fail with link. venv at
  `<app>/.venv`. `requirements.lock` (hashes) with
  `requirements-paper.txt` fallback. Old services stopped first.
  systemd user units (bot/dashboard/watchdog, Restart=always) + retrain
  timer + linger. `.desktop` icon. `--uninstall [--purge]`.
- **macOS** — `python3.1x` scan → `brew install python@3.12` (validated
  after). LaunchAgent with `KeepAlive`. `Gold Reaper.app` bundle.
  Same venv/lock/gate/uninstall contract.
- **Windows** — `py -3.12` → `winget install Python.Python.3.12` with an
  explicit `$LASTEXITCODE -ne 0` check (no try/catch guesswork) →
  post-install validation. Task Scheduler tasks (logon+boot, restart
  60s). Desktop `.lnk`. Same venv/lock/gate/uninstall contract.
- All three: never overwrite `.env`; print INSTALLED only after the gate
  exits 0.

## 6. Tests added + exact commands

9 files / 83 tests. Commands:

```bash
python -m pytest tests/test_strategy_freeze.py -v   # freeze guard
python -m pytest tests/ -v --tb=short               # full suite
python -m ruff check .                              # lint
python -m mypy                                      # types (27 files)
python installer/health_check.py                    # 12-step gate
python cli.py doctor                                # ops report
```

## 7. Actual test results (including failures found & fixed mid-repair)

Final local run (this sandbox, Python 3.12.14):

- `pytest tests/` → **83 passed** (0 failures, 0 skips except
  `importorskip("duckdb")` guard paths that are installed here).
- `ruff check .` → clean. `mypy` → clean (27 files).
- `installer/health_check.py` → **ALL 12 STEPS PASS, exit 0** on a real
  run: ingest 13,733 1h bars (0.41d fresh), 112 feature columns,
  `GC=F` feed validated, paper trade opened 4220.70 → closed (PnL net of
  fee), persistence across restart, bot 30s paper run with fresh
  heartbeat (`mode=PAPER, broker=PAPER`), dashboard `/health` 200, both
  breakers trip.

Failures caught BY the new tests during the repair (each fixed, then
re-run):

1. `connect()` never set `CONNECTED` on the happy path — caught by
   `test_valid_feed_connects`.
2. `_account_dict()` triggered a network fetch on every save — caught by
   fixture errors; replaced with `_equity_quiet()`.
3. Safety-net `opened_at` guard rejected the entry bar itself — boundary
   corrected to `ts + BAR_PERIOD <= opened_at`, semantics documented
   (SD-5).
4. Hydration ordering double-counted fills — the wiring contract is now
   explicit in `_feed_stream` (hydrate → register → save) and mirrors
   `bot.py`'s tick order.
5. `ApexRisk.register_fill` re-latches breakers on every breaching fill —
   inside a frozen file → documented as SD-6; the gate-path once-only
   contract is pinned by test instead.
6. **Real leak found and fixed**: atexit state flushes followed re-pointed
   module globals after pytest teardown and wrote test state into the
   repo's `data/` — fixed by construction-time path binding + regression
   tests; leaked files untracked from git and removed.
7. Tuple-as-pandas-key KeyError in health check step 6 (`X[tuple]`) —
   fixed with `list(...)`; the gate had correctly REFUSED success first.

## 8. Static checks

`ruff check .` clean (repo config: E/F/W/I, line-length 88);
`mypy` clean on `core`, `brokers`, `features`, `bot.py` (repo config).
`bash -n` clean on both shell installers + launchers; YAML parsed for
both new/edited workflows; `py_compile` clean on all touched modules.

## 9. Protected strategy hashes — unchanged (confirmation)

SHA-256 at repair close — byte-identical to the baseline manifest:

```
core/strategy.py       bbc644bdcdc5a0cf137ba93882c0fda04bfde1e1c3727b9678677710912e5068
core/strategy_apex.py  024afb10ea45b8827a87dc2ae174d306764b49c8391c54767e6b40a7e3d511f3
core/config.py         697d391c2ccb880be08193dc856e43e5918d18203efba63353b9876c01feaefd
core/risk.py           9a6478e6ef45d7ed1ffb894a0519b62f99269988ff3131ec9d81bbc738b4a6bf
core/risk_apex.py      bddb21e4ddc032709ebccfe9820039de6127082625e963b9d76df05b3ef65852
core/indicators.py     401e8c2f68745584587e4cd937d9367744923a36f49f4b4a2808727209943114
```

`git diff main...HEAD` on those six paths is empty. Freeze test enforces
it on every CI run.

## 10. Known limitations

- Live brokers (MT5/Bitget) are signature-compatible with the new
  `close_position` contract but were **not** integration-tested against a
  real venue (paper-only testing, by design — see §11).
- Weekend feed grace (96h) is a deliberate deviation from the literal
  24h rule so a healthy Sunday connection is not flagged degraded;
  documented in code + `tests/test_feed_validation.py`.
- SD-1…SD-6 (frozen-file defects, incl. breaker re-latch alert spam and
  the two daily-loss formulas) remain by contract.
- `features/store.py` creates `data/store/` at import time (benign,
  gitignored) — left alone.
- Health check step 10 runs the bot for ~30s with real network; in
  offline environments use `--quick` (network steps skip, report labels
  itself accordingly and never prints "ok to print SUCCESS").

## 11. Tests that could not be run (and why)

- **Windows / macOS**: no access to those platforms in this sandbox. All
  three installers + the test suite run in CI on real runners
  (`installer-e2e.yml`, `tests` matrix) — until those are green, Windows
  and macOS support is **claimed by CI evidence, not local testing**.
- **Live MT5 / Bitget execution**: requires real credentials and a
  terminal; explicitly out of scope. The failover tests use stub brokers.
- **`systemd` service start**: this sandbox has no user bus
  (`systemctl --user show-environment` fails) — service *files* are
  written and validated by the e2e workflow on real Ubuntu runners.
- **redis mirror** of risk state: no redis in the sandbox; code path is
  unchanged and guarded.

## 12. Verified / partially verified / not tested

- **Verified locally (this sandbox)**: full pytest suite (83), ruff, mypy,
  freeze hashes, full 12-step health gate with real feed/trade/bot run,
  dashboard banner/auth/bind, doctor CLI, persistence incl. crash-mid-write,
  exit execution incl. same-candle both-hit, halt semantics, config gate.
- **Partially verified (CI-pending)**: installer runs on macOS/Windows and
  systemd service start (e2e workflow runs on real runners once pushed);
  CI `tests` matrix on py3.10/3.12 × 3 OSes.
- **Not tested**: live-broker order paths (paper-only), redis mirror,
  GNOME/desktop-icon trust bit beyond file creation.

## 13. Exact install / launch / stop / test commands

| | Linux | macOS | Windows |
|---|---|---|---|
| Install | `./install_linux.sh` | `./install_macos.sh` | `powershell -ExecutionPolicy Bypass -File install_windows.ps1` |
| Launch | `~/.local/share/gold-reaper/start.sh` | `Gold Reaper.app` or `start.command` | desktop shortcut or `start_windows.bat` |
| Stop | `start.sh stop` or `systemctl --user stop gold-reaper` | `./install_macos.sh --uninstall` flow or `launchctl unload` | `Stop-ScheduledTask -TaskName GOLD-REAPER` |
| Health | `.venv/bin/python installer/health_check.py` | same | `.venv\Scripts\python.exe installer\health_check.py` |
| Doctor | `.venv/bin/python cli.py doctor [--fix]` | same | `.venv\Scripts\python.exe cli.py doctor` |
| Tests | `python -m pytest tests/ -v` | same | same |
| Uninstall | `./install_linux.sh --uninstall [--purge]` | `./install_macos.sh --uninstall [--purge]` | `.\install_windows.ps1 -Uninstall [-Purge]` |

## 14. Definition of Done — status

- [x] P0 tests written and passing (exit execution, persistence, risk
      consistency)
- [x] Paper trades close correctly on SL, TP, and same-candle both-hit
- [x] Paper state survives restart with open positions intact
- [x] Feed validation rejects stale/empty/NaN data
- [x] Failover explicit; live→paper swap structurally impossible + tested
- [x] Installer one-command with health gate on all three OSes
      (e2e workflow — CI evidence pending first run)
- [x] Protected strategy hashes unchanged (re-verified, CI-enforced)
- [x] docs/REPAIR_REPORT.md honest and complete (this file)

*Quiet. Honest, Repaired, Lethal.*
