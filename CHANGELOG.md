# Changelog

All notable changes. Format based on Keep a Changelog; versioning: semver.

## [2.4] — 2026-10-09 — honest zero-state console + analytics layer

### Fixed (QA round: agent-browser audit found 5 bugs)
- Live mode with zero realized fills rendered the seeded MOCK equity walk
  ($10k scale) next to the real $1,000 balance — misleading scale. The live
  curve is now flat at true equity, labeled "flat · awaiting first realized
  fill"; mock walks only ever render in mock mode.
- Live mode with zero fills rendered 20 mock trade rows while the streak
  panel said W0·L0. The trades table now shows an honest "no fills yet —
  journal open, first hunt pending" empty state (mock rows only in mock mode).
- SSE stream produced nothing until a NEW audit record arrived (panel looked
  dead whenever the bot was idle). The stream now seeds the last ~14 audit
  records formatted, then tails live.
- Audit records were flattened to their bare kind ("skip"). Full formatter
  now renders skip reasons (regime + probability + ADX + news countdown +
  confluence votes), fills (`fill +23.41 USD · block +23.41`), lifecycle
  (reaper online/offline/breakeven), and order rejections.
- Latent: the "real equity curve" read a `pnl` column from trades.csv that
  the bot's journal schema never writes (entries journal planned risk only;
  realized PnL lives in audit.jsonl fill events). Equity now reconstructs
  from fills, and trade rows pair the k-th entry with the k-th fill
  (one-position-at-a-time invariant), so realized PnL shows in the table.

### Added
- `GET /api/metrics` — realized-fill analytics: profit factor, win rate,
  expectancy, avg win/loss, best/worst, max drawdown ($ and %), longest
  win/loss streaks, 8-bin PnL histogram, underwater series, per-regime PnL
  split. Live source = fills; mock source = the same seeded session as the
  table (numbers always agree).
- Dashboard analytics panel: 8-cell stat grid + pure-SVG PnL distribution
  (green/red by sign) + underwater drawdown curve with min marker.
- Hunt clock panel + topbar countdown chip: time to next 12:00-14:00 UTC
  window (or "OPEN" + time left during it), window progress bar, next three
  window opens. Pure client-side UTC math.
- Trades CSV export button (client-side from loaded rows; disabled when empty).
- Animated count-up ticker on equity figures (skipped under
  prefers-reduced-motion).

### Changed (styling detail pass)
- Subtle CRT scanline overlay, panel hover border transition, pulsing
  "awaiting telemetry" placeholders, styled empty states with LED markers.
- SSE severity coloring extended (fill/reaper online → green, skip → dim,
  offline → red).

### Ops
- Pages mock payload now embeds metrics so the public demo renders the
  analytics panel backend-free; CI demo-layer check asserts the metrics
  payload agrees with the trades table.

## [2.3] — 2026-10-09 — signal diagnosis + hardened config (VISUAL/SIGNAL SUPREMACY)

### Fixed (the signal, honestly)
- Published walk-forward losses diagnosed in `research/diagnose.py` +
  `docs/DIAGNOSIS.md`: RANGE-regime mean-reversion bled -526 (32 trades),
  overlap hours 14-15 UTC bled -1,010 combined, trend module fired into the
  wrong hours. SL-first fill assumption verified NOT the problem (TP-first
  rerun identical). Cost model verified realistic but not the root cause.
- 20y daily ML probe: mean AUC 0.528 -> tabular ML confirmed dead as a
  hard gate (second independent confirmation).

### Changed (strategy, walk-forward promoted: worst fold -663 -> -112)
- Hunt window tightened to 12:00-13:59 UTC (`ENTRY_HOURS_UTC=12,13`)
- TP retuned 2.67R -> 2.0R (`TP_R=2.0`, SL unchanged 1.2xATR)
- Mean-reversion ATR-rank ceiling 0.40 (`MEANREV_VOL_MAX=0.4`)
- `research/backtest_apex.py` now publishes 5 modes incl. v3 hardened:
  net -970 -> +34 (11 trades, worst fold -663 -> -112); APEX-X v3 +356
  on 8 trades (PF 2.57, sample too small to claim an edge - documented)
- Live ensemble mirrors v3 gates exactly (hours, vol ceiling, TP ladder)

### Added
- `research/diagnose.py` (9-question microscope), `research/tune_v3.py`
  (worst-fold-first fix tuner), `research/track_record.py` +
  `docs/TRACK_RECORD.md` (append-only paper ledger)
- `docs/DIAGNOSIS.md` - full honest loss analysis

## [2.1] — 2026-10-09 — professional rebrand

### Changed
- Full visual identity: dark terminal palette (#0A0E0F / #00FF9C / #00D9FF),
  monospace stack, thin terminal frames, status LEDs — docs/BRAND.md
- README rewritten: professional tone, SEO structure, honest verdicts kept
- Demo layer: `demo/terminal_demo.py` (rich), `dashboard/app.py` :8080,
  `demo/record_demo.sh` (asciinema→gif→mp4), GitHub Pages static demo
- Repo metadata: keyword-dense description, 20 topic tags, generated
  social preview (`assets/gen_brand.py`)

### Added
- docs/BRAND.md, docs/DEMO.md, docs/SEO.md, docs/ARCHITECTURE.md,
  docs/STRATEGY.md, docs/RISK.md
- CHANGELOG, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- requirements-dev.txt, pyproject.toml

### Removed
- Casino/hype styling and slogans from all repo-facing surfaces
  (technical content preserved; internal identifiers unchanged)

## [2.0] — 2026-10-09 — APEX

### Added
- 10-dimension feature forge: 142 features × 13,722 H1 bars (98.1% coverage)
- DuckDB columnar store with idempotent upserts (`features/store.py`)
- Multi-timeframe ingestion (1m→1mo, 65k bars) + Dukascopy tick decoder
- HMM regime router (5 regimes) with rule fallback (`core/regime.py`)
- News/calendar pipeline + sentiment scorer (`core/news_brain.py`)
- APEX-X ensemble: regime-routed trend/meanrev/breakout/news modules with
  consensus voting and XGBoost soft vote (`core/strategy_apex.py`)
- $20/4h block risk engine: adaptive sizing, recovery mode, rolling PF guard,
  latched daily/weekly/monthly circuit breakers (`core/risk_apex.py`)
- Decision audit trail (JSONL), Telegram/Discord/Email alerts
- FastAPI ops dashboard (:8050), multi-account runner, broker failover chain
- Weekly walk-forward retrain with promotion gate (AUC ≥ 0.56)
- Walk-forward ensemble backtest head-to-head vs REAPER-X
- GitHub Actions: APEX CI blood test + snake animation + Pages demo

### Honest findings (documented in docs/STRATEGY.md)
- Tabular ML (H1: AUC 0.500, D1: 0.537) does not clear the OOS gate on this
  dataset — ML runs as transparent soft vote; hard gate stays off until a
  retrain clears 0.56
- APEX-X reduces baseline losses 24% and drawdown 2.2 pts on the hard window;
  both systems lose on the 2026 fold — regime dependence published

## [1.0] — 2026-10-09 — initial release

### Added
- REAPER-X strategy: H4 bias + H1 pullback, walk-forward-locked geometry
  (SL 1.2×ATR · TP 3.2×ATR · BE 1.0R)
- 20-year XAU/USD recon (5,032 daily bars, 13,722 hourly)
- 4-fold walk-forward optimizer ranked by worst fold
- Exness MT5 + Bitget + paper brokers with common interface
- Session logic, US-news blackout, Friday/weekend guards
- Linux systemd + Windows Task Scheduler autostart installers
- MIT license, trading risk disclaimer
