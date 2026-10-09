# Risk — the survival layer

Trading leveraged gold can lose more than your deposit. This document is the
contract between the system and your account.

## Position sizing

```
risk_fraction = clamp(0.5% .. 2.0%,  20 / (avg_R × equity) × 100)
ounces        = (equity × risk_fraction) / |entry − stop|
```

- Fixed-fractional: risk scales with equity, compounding up **and** down
- Neverional cap: ≤ 50× equity exposure, hard-clamped
- Broker conversion handles lot steps, min/max volumes per symbol

## Circuit breakers (latched — require manual reset)

| Horizon | Threshold | Effect |
|---|---|---|
| Day | −3% of day-start equity | full stop |
| Week | −7% of week-start equity | full stop |
| Month | −15% of month-start equity | full stop |

While latched, the bot refuses every entry. Reset is explicit and audited:

```bash
python bot.py --reset-breakers
```

## Session guards

- **$20/4h block target** — block closes when banked; max 3 attempts per block
- **Loss-streak cooldown** — 3 consecutive losses → stand down
- **Rolling PF monitor** — 30-day PF < 1.2 → risk ×0.5 until PF ≥ 1.3
- **Recovery mode** — losing day → next blocks at 50% size
- **News blackouts** — no entries 12:25–12:35 / 13:25–13:35 UTC
- **Weekend lock** — no entries within 2h of Friday close, none on weekends
- **Confidence floor** — ensemble confidence ≥ 0.55 required

## Structural protections

- SL/TP live on the **broker's server**, not the bot — a crash cannot strip
  protection from an open position
- Magic number `666666` — the bot only ever manages its own orders
- Paper mode is the factory default; live requires two deliberate `.env` edits
- Every fill, veto and breaker event lands in `data/audit.jsonl`

## Honest limitations

- Breakers cap *daily* damage; a sequence of latched days still loses money
- Slippage/spread widen during news and low liquidity — the backtest models
  this imperfectly with fixed assumptions
- Broker-side execution (requotes, partial fills) is outside the bot's control
- The 2026 fold of the walk-forward was negative. Regimes change. Size down
  when the PF guard says so, and read `docs/STRATEGY.md` before scaling.

**No component of this project guarantees profit. Educational software. You
are responsible for your own capital.**
