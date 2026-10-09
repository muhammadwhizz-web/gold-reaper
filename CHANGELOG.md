# Changelog

All notable changes. Format based on Keep a Changelog; versioning: semver.

## [3.1] — 2026-10-10 — session intelligence

### Added
- **By-session P&L rollup**: the analytics panel gains a "by session ·
  net / fills / win rate" split, aggregated client-side from the
  journal — each session gets a glowing inline bar scaled by |net|
  (green profit / red loss), fill count, signed net, and win rate.
  This is the repo's thesis rendered as data: the overlap window is
  supposed to be the one that pays, and now the console shows whether
  it actually does. Honest empty state ("no session attribution yet")
  before the first close.
- **Live underwater from the real walk**: with zero realized fills the
  metrics payload has no underwater series — but the live equity walk
  (session marks + fills) IS real data, so the analytics underwater now
  derives honestly from the walk (running-peak dd%) instead of showing
  nothing. Only when `equity_source` is non-flat and the walk actually
  moves; a tooltip states the derivation ("derived from the live
  equity walk (session marks + fills)"). A flat $1,000 account still
  shows nothing — nothing happened.
- **Journal session chips**: the trades table's session cell renders as
  a bordered tag — `LONDON_NY_OVERLAP` wears the brand green, London
  cyan, NY amber, everything else dim. The hunt window is scannable at
  a glance in a wall of rows.

### Changed
- Styling pass: drawer stat cells highlight on hover; rollup bars and
  session chips live inside the existing terminal palette (no new
  colors beyond the console's established green/red/cyan/amber set).
- Pages mock replays the v3.1 contract (session rollup is computed from
  trades the mock already ships); CI hermetic checks assert the new
  markers (`mxSessions`, `renderSessions`, `sessTag`, `walkToUnderwater`,
  `LAST_STATE`, `ss-overlap`, `sbar`).

## [3.0] — 2026-10-10 — live-curve completion · operator ergonomics

### Added
- **Session-mark toast**: when a NEW session-boundary equity mark lands
  on the live walk, the console drops one quiet info toast —
  "session mark · equity $1,001.23 @ 13:00 UTC". The first paint never
  toasts (a boot replay would be noise, not signal); fill-only index
  shifts stay silent. Closes the v2.9 feedback loop: the operator now
  *sees* the curve acquiring a snapshot, not just the dot.
- **Crosshair on the equity curve**: a dashed vertical guide rides the
  chart's native hover (Chart.js plugin, zero new listeners), and the
  tooltip now reports the walk's Δ against the session-open baseline —
  "equity $1,012.34 · Δ +$12.34", plus the existing "· session mark"
  flag on snapshot points.
- **Walk delta chip**: the equity-source chip gains the live walk's
  net movement — "curve · fills + session marks · Δ +$12.34" (hidden
  when the walk is flat, so an honest $1,000 line says nothing).
- **SSE exponential backoff**: on stream error the console now closes
  the socket itself and re-dials with a real backoff ladder
  (1s → 2s → 4s → 8s → 16s → 30s cap) instead of relying on the
  browser's fixed ~3s retry — a dead server stops getting hammered.
  The 5s state poll keeps data flowing throughout; the sled chip keeps
  its push/poll LED and now carries the reconnect plan in its tooltip
  ("reconnecting in Ns · attempt M"); counters reset on reattach.
- **Drawer copy-JSON**: the trade drawer gains a "copy json" button and
  a `c` shortcut (drawer open) — one keystroke puts the raw journal row
  on the clipboard, with toast acknowledgment ("trade json copied · row
  N") and an honest warning if the browser blocks the clipboard.
- **Focus-visible rings**: every button in the console draws a green
  focus ring when keyboard-navigated (accessibility pass — the console
  is keyboard-first, it should look like it).

### Changed
- QA shim (`qa-tools/build_gr_qa.sh` replica) exercises the full v3.0
  transport lifecycle: the FakeES stub now supports `close()` and
  replays its seeded log tail once, so the backoff re-dial renders
  without duplicating boot lines.
- Pages mock replays the v3.0 contract (walk Δ, crosshair-ready
  tooltips) so the demo shows the full layer; CI hermetic checks assert
  the new console markers (xhair, EQ_MARK_SEEN, BACKOFF_S, dCopy,
  copyTradeJson, walkDelta).

## [2.9] — 2026-10-10 — session equity marks · transport LED · r-band drawer

### Added
- **Session-boundary equity marks**: the bot appends one real equity
  snapshot per session transition to `data/equity_marks.csv`
  (append-only; `bot._append_equity_mark`). The dashboard merges marks
  with realized fills into a time-ordered live curve
  (`app._equity_history_live`) and the console switches from a bar-index
  axis to a real HH:MM UTC axis, drawing a glowing dot on every session
  mark (tooltip: "· session mark"). The live equity curve finally moves
  between fills — honestly: only real broker equity at real timestamps,
  no interpolation, no fabrication. Without marks the console falls back
  to the legacy index axis; `state.equity_source` now reports
  `marks+fills` / `fills` / `flat` and the equity chip explains itself.
- **Transport LED**: the stream panel header shows which telemetry
  transport is winning — `PUSH` (green outline) once SSE delivers, or
  the plain fallback label. Losing the link drops it to poll with a
  "telemetry link lost · polling fallback" amber toast; reconnection
  fires "telemetry reattached · push restored" once (dedup guard, no
  toast spam while EventSource retries).
- **R-multiple band in the trade drawer**: every drawer now renders the
  trade's R multiple on a -1.5R..+3.2R scale — hatched loss zone left of
  entry, ticks at entry and +2R target, a glowing marker at the trade's
  own R (green wins / red losses, value chip above). Journal data that
  was already on the wire, finally visualized.
- **`e` shortcut**: exports the journal CSV from the keyboard (same path
  as the export button); footer hint and `?` help overlay updated.

### Changed
- Console: `updateEqChart` accepts the optional time axis + mark indices;
  tooltip titles read "HH:MM UTC" instead of "bar N" when marks exist.
- Pages mock replays the v2.9 contract (time axis + mark dots) so the
  demo shows the full layer; CI hermetic checks assert the marks merge,
  the honest no-marks fallback, and all new console markers.
- `.tfill` hatch opacity doubled (0.022 → 0.045) so the journal's dead
  space reads as intentional texture instead of a black hole.

## [2.8] — 2026-10-09 — toast feedback · j/k journal nav · regime sequence

### Added
- **Toast feedback layer**: console actions now acknowledge themselves —
  kill-switch engage/release (amber/green), local preview pause, journal
  CSV export with row count, and the boot attach notice
  ("console attached · mock replay" / "· live telemetry"). Toasts stack
  bottom-right, auto-dismiss at 3.2s, cap at four, and announce through
  an `aria-live="polite"` region. Server-side kill-switch flips that
  arrive via polling/SSE toast once — change detection lives inside
  `applyStandby`, so every path (button, poll, state push) reports once.
- **j/k journal navigation**: `j`/`k` walk the trades table row by row
  with an amber cursor and row-number glyph, `o`/`Enter` opens the
  highlighted row's detail drawer, and `j`/`k` keep navigating the
  drawer live while it is open (one shared cursor with click-to-open).
  `Esc` closes drawer and clears the cursor; `scrollIntoView` stays
  `nearest` so walking never yanks the page.
- **Regime sequence strip**: the analytics panel gains a chronological
  one-segment-per-fill strip (green TREND_UP / red TREND_DOWN / olive
  RANGE), newest segment outlined, each with a `date · regime · pnl`
  tooltip. Honest zero-state when the journal is empty.
- **Equity end-value chip**: a Chart.js plugin draws a glowing marker on
  the last equity point plus a small price tag, so the curve's current
  value reads without a hover.

### Changed
- Keyboard help overlay and footer hint now list `j` `k` `o`; README
  dashboard row updated to match.
- The trades-panel footer (`.tfill`) carries a faint 45° hatch so the
  dead vertical space below a short journal reads as intentional.

## [2.7] — 2026-10-09 — SSE state push + favicon LED + trade detail drawer

### Added
- **SSE state push**: `/api/stream` now emits named `state` events (~6s)
  carrying the full state payload. The console consumes them as the
  primary state source and widens its poll to a 15s fallback once a
  push is observed; on stream error the poll narrows back to 5s.
  Named events leave the default `message` log-tail channel untouched.
- **Favicon LED**: the browser-tab icon is drawn on a canvas and mirrors
  console status — pulsing green while the hunt window is open, amber
  under standby, red when offline, dim dot otherwise (reduced-motion
  users keep the shipped static icon). Joins the existing tab-title
  countdown as a background-tab status mirror.
- **Trade detail drawer**: clicking a trades-table row slides in a
  drawer with the row in a label/value grid (time, side, size, entry,
  realized pnl, r-multiple, regime, session) with sign coloring, a
  live-vs-journal footnote, and selection highlight that survives
  re-sorts. Esc / backdrop / ✕ all close it.
- **Keyboard help overlay** (`?`) listing every shortcut, plus a console
  version chip in the footer sourced from `pyproject.toml` via the new
  `state.version` field (single source of truth).

### Changed
- Panel HUD corner brackets now idle at 34% opacity and brighten to
  full green on hover (they were permanently solid).
- Hermetic CI check extended: console-version + SSE-state-event
  contracts asserted server-side; static markers for the push
  listener, favicon LED, drawer, help overlay, version chip.

### Fixed
- `mypy .` (full tree) now clean: 4 pre-existing errors outside the CI
  scope in the Windows-only `watchdog.py` / `system_tray.py` (stdout
  `reconfigure` narrowing, tray stop-callback signature, Event used
  before assignment).

## [2.6] — 2026-10-09 — heartbeat position snapshot + ledger heat strip

### Added
- **Open position on the console, for real**: the bot's heartbeat now
  carries an open-position snapshot (`side/size/entry/sl/tp/opened/be/
  trail/age_h`), and the live state endpoint passes it through verbatim —
  the position card finally renders the actual running position (with BE
  and TRAIL chips and an age tooltip) instead of permanent "flat ·
  scanning". Stale heartbeats keep showing their last real snapshot; the
  bot chip already flags staleness. CI asserts the pass-through contract
  hermetically (fresh snapshot renders, deleted snapshot renders flat).
- **14-day paper-ledger heat strip**: bar height carries the day's
  absolute net, color carries the sign, hover reveals date · trades ·
  net. Renders from the append-only `docs/TRACK_RECORD.md` on both the
  FastAPI console and the GitHub Pages demo.
- **Browser-tab countdown**: `document.title` mirrors the hunt clock
  ("hunt in 03:12:44 · GOLD//REAPER" / "hunt open · 00:47:03" / "STANDBY
  · GOLD//REAPER") so a background tab still reports the window state.
- Trades panel gets an "end of journal · append-only" footer line
  anchoring the panel bottom (previously dead vertical space).

### Fixed (QA round: agent-browser audit of Pages demo + live harness)
- QA harness: the static payload script tag was never executed
  (`type="application/json"` is data, not code) — the live-mode harness
  rendered placeholders; payload is now injected as executable JS.
- Two pre-existing mypy errors in `dashboard/app.py` (untyped `hist`
  dict, `float(None)` on adx) — now clean under strict local mypy even
  though `dashboard/` is outside the CI mypy scope.

## [2.5] — 2026-10-09 — server-side kill-switch + honest live telemetry

### Fixed (QA round: agent-browser audit + code review)
- Row D (analytics + hunt clock) sat outside `<main>` since v2.4, so the
  8/4 grid split was ignored and both panels rendered full width stacked.
  Moved inside the grid; verified 822/404 side-by-side in the browser.
- Live mode fabricated broker telemetry: "Paper 1ms" latency and a hardcoded
  TREND_UP 71% regime rendered even when the bot had been silent for hours.
  Live state now comes from `data/heartbeat.json` — a stale/missing
  heartbeat renders "bot stale"/"no bot heartbeat" chips, an "awaiting"
  regime panel, an honest radar empty state, and broker LEDs with no
  invented latency. Nothing is fabricated while the bot is silent.
- Trades table lost the mode on sort (empty live table showed the wrong
  empty-state message after clicking a header).
- CSS: removed a duplicated 65-line style block; fixed `.lrow .ln2`
  referencing an undefined `--fg2` variable.
- Price chip showed "XAU 0.00" when the heartbeat carried no price
  (`Number(null)` is 0); null price now hides the chip.

### Added
- **Real kill-switch**: the console's standby button POSTs `/api/standby`,
  which writes `data/standby.flag`; the running bot skips NEW entries while
  the flag exists (exits/position management keep running). GET+POST
  `/api/standby`, `bot.standby_on()` / `bot.set_standby()`.
- **Bot liveness chip + richer heartbeats**: `_write_heartbeat` now carries
  last price, session and the last classified regime dict; the console shows
  `bot PAPER · Xs ago` (green), `bot stale · Xm ago` (amber) or
  `no bot heartbeat` (grey).
- **News radar panel** + `GET /api/news`: upcoming events from the bot's
  cached ForexFactory calendar with impact coloring (high/medium/low) and
  countdowns; meta shows the next high-impact event or "clear".
- **Session map**: 24 h UTC strip in the regime panel (asia/london/overlap/
  ny/off bands, green hunt-window outline, live "now" cursor, hour ticks).
- **R-multiple column** in the trades table (`pnl / planned risk` — the
  locked geometry's honest unit) with numeric right-alignment for
  size/entry/pnl/r columns.
- **Drawdown shading** on the equity curve: dashed running-peak line with a
  red fill between peak and equity (invisible while equity IS the peak).
- Pages mock embeds news + the new state fields; CI hermetic check now
  asserts the kill-switch round-trip, news parsing, R column, and that the
  live branch fabricates neither regime nor dims.

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
