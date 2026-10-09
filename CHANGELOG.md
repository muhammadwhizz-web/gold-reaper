# Changelog

All notable changes. Format based on Keep a Changelog; versioning: semver.

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
