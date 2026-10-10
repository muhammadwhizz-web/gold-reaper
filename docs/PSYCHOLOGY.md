# PSYCHOLOGY — how the engine measures market psychology

> `core/psychology.py` (PsychologyEngine, name `HPE-PSY`). One job: turn each
> H1 bar into a measurable psychological state — a 0-100 fear/greed composite,
> five regime tags, and hard vetoes the HPE ensemble must obey. Every rule in
> this document is implemented verbatim in the module docstring and pinned by
> tests. Honesty rule: **confidence reflects data coverage; absent data never
> fakes a signal.**

## 1. The 27 `psy_` columns

| group | columns |
|---|---|
| price natives | `psy_rsi`, `psy_mom_z`, `psy_vol_spike`, `psy_range_atr` |
| capitulation | `psy_capitulation_score`, `psy_capitulation` |
| euphoria | `psy_euphoria_score`, `psy_euphoria` |
| posture | `psy_denial`, `psy_crowding_score` |
| streak | `psy_streak_extreme`, `psy_streak_signed` |
| external (optional) | `psy_vix`, `psy_vix_z`, `psy_dxy_ret5`, `psy_us10y_ret5` |
| external (optional) | `psy_gld_flow_z`, `psy_funding`, `psy_funding_z`, `psy_cot_net` |
| news / calendar | `psy_news_sent`, `psy_news_conf`, `psy_min_to_event` |
| news / calendar | `psy_event_relevance` |
| output | `psy_fear_greed`, `psy_confidence`, `psy_regime_tag` |

Supporting quantities (`mom_z`, `vol_z`, `range_atr`, `close_pos`,
ATR pct-rank) are computed inline from the same bar frame; nothing is read
from the future. `mom_z` is clipped to ±6 and the clipped value is used
everywhere downstream. `psy_cot_net` is a passthrough with no composite
weight (the spec defines none); `psy_streak_signed` is exposed so the
composite's trend-persistence input stays auditable.

## 2. The fear/greed composite

Weighted mean over **available components only** — a missing component drops
out and the remaining weights are renormalized. `psy_confidence` is the
covered weight (0..1): an all-natives bar sits near 0.7, a bar with every
external series wired sits at 1.0. The composite is clipped to 0..100.

| component | weight | map |
|---|---:|---|
| momentum | 0.25 | `50 + 50 * tanh(mom_z / 3)` |
| rsi | 0.15 | raw RSI(14), clipped 0..100 |
| vol regime | 0.15 | `100 - 100 * ATR pct-rank(500)` — high vol = fear |
| streak | 0.15 | `50 + 50 * tanh(signed_streak / 5)` |
| vix | 0.10 | `50 - 12 * vix_z` |
| dxy | 0.10 | `50 - 40 * clip(ret5 / 1.5, ±1)` |
| funding | 0.10 | `50 + 25 * clip(funding_z, ±2)` |

External z-scores are 20-bar rolling z-scores; zero dispersion produces NaN
→ the component is **unavailable**, never faked. The ATR pct-rank window is
the trailing 500 bars (min_periods 100, so mid-frame rows get the component
honestly). Crowding never moves the composite level — it is a magnitude-only
confidence discount consumed by the ensemble.

## 3. Detectors (exact rules)

**Capitulation** — panic flush on a wide, high-volume down bar:

- trigger bar `t`: `vol_z > 2` AND `range_atr >= 2.0` AND close < open AND
  the close sits in the bottom 25% of the bar (`close_pos <= 0.25`)
- score `t = min(1, vol_z / 4)`; all other bars score 0
- **reversal hold**: if the bar after a trigger closes up
  (`close[t+1] > close[t]`), the score is forced to **1.0 on bars t+1 and
  t+2** — a confirmed reversal is the strongest capitulation signal the
  engine emits
- `psy_capitulation = 1` wherever score > 0

**Euphoria** — parabolic climax:

- `mom_z >= 3.0` AND `rsi14 >= 80` AND `range_atr >= 1.8`
- score `= min(1, mom_z / 5)`; `psy_euphoria = 1` wherever score > 0

**Denial** — post-shock consolidation, volatility drained:

- ATR pct-rank (500) `< 0.35` AND `adx14 < adx14.shift(10)` AND `vol_z < 0`

**Crowding** — trend-chase, magnitude only:

- `adx14 > 25` AND `abs(mom_z) > 2` AND `vol_z > 0.5`
- score `= min(1, (adx14 - 20) / 30)`; never moves the composite

## 4. Regime tags

| tag | composite |
|---|---|
| PANIC | <= 15 |
| FEAR | <= 40 |
| NEUTRAL | <= 60 |
| GREED | <= 85 |
| EUPHORIA | > 85 |

Overrides, applied in order: a capitulation flag forces the tag **>= one step
down toward PANIC** (floored at PANIC); a euphoria flag then forces
**EUPHORIA** (applied last, overrides the capitulation step). A NaN composite
maps to NEUTRAL, so the tag is always exactly one of the five.

## 5. Veto table

`veto(psy_row, side)` is a hard gate — the ensemble cannot override it.

| side | veto 1 | veto 2 | veto 3 |
|---|---|---|---|
| LONG | euphoria flag | `fear_greed >= 85` | event <= 30 min @ rel >= 0.6 |
| SHORT | capitulation flag | `fear_greed <= 15` | same event rule |

The event veto is a high-impact calendar event within 30 minutes at gold
relevance >= 0.6 (the same rule both sides).

Failure semantics, documented on purpose:

- NaN components **fail open one by one** — a missing VIX never vetoes.
- NaN `psy_fear_greed` marks the whole row unavailable →
  `(False, "psy unavailable")` (no veto, and the ensemble records it).
- An unknown side raises `ValueError` — fail loudly, never silently trade.

## 6. Data honesty

- **Price-native detectors always work**: capitulation, euphoria, denial,
  crowding, streaks and the native composite components need nothing but the
  H1 OHLCV frame.
- **External series are optional extras** — keys `vix`, `dxy`, `us10y`,
  `gld_vol`, `funding`, `cot_net`. Any subset may be supplied; absent keys
  become NaN columns and degrade to neutral (their composite weight drops
  out, `psy_confidence` drops with them). Nothing is interpolated into a
  fake level.
- **News sentiment currently returns 0.0 / 0.0** (`psy_news_sent`,
  `psy_news_conf`) — no news table is wired yet. The ensemble's news module
  therefore **abstains rather than fabricates** (see [HPE.md](HPE.md) §2).
  Calendar features (`psy_min_to_event`, `psy_event_relevance`) work from the
  economic-calendar table when present: minutes to the NEXT `is_high` event
  via searchsorted, relevance recorded while that event is <= 240 min out.
  The calendar is legitimately forward-known, so this is not lookahead.

## 7. Reproduce

```bash
python -m pytest tests/test_psychology.py -v     # 7 deterministic tests
python -c "from core.psychology import PSYCHOLOGY_COLUMNS as c; print(len(c))"
```

The tests cover composite bounds + confidence behavior, capitulation and
euphoria detection, the full veto table, missing-extras degradation, calendar
features and a no-lookahead check (all 27 columns identical on a prefix
frame).
