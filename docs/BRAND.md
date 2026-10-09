# GOLD//REAPER — Brand Manual

> One test for every visual decision: **would an elite quant desk ship this?**
> If it shouts, it goes.

## Palette

| Role | Hex | Usage |
|---|---|---|
| Background | `#0A0E0F` | page/terminal background (near-black, slightly green-cast) |
| Panel | `#0D1214` | cards, consoles |
| Line | `#1B2327` | hairline borders, table rows |
| Primary | `#00FF9C` | terminal green — status, headings, key numbers |
| Secondary | `#00D9FF` | cyan — session tags, secondary data, links |
| Warning | `#FFB000` | amber — stops, standby states |
| Danger | `#FF3B3B` | red — losses only, sparingly |
| Muted | `#5A6B6F` | labels, metadata, footnotes |
| Text | `#C9D1D9` | body text |

## Typography

JetBrains Mono → IBM Plex Mono → Fira Code → `ui-monospace, Consolas, monospace`.
No other fonts. Headings are monospace with wide letter-spacing, never colored
blocks or gradients.

## Motifs

1. **Thin terminal frame** — 1px rectangle with 14px corner ticks. Never chunky boxes.
2. **Status LEDs** — `● HUNTING` (green, pulsing), `● STANDBY` (amber), `● LOCKED` (red). LEDs state facts, not feelings.
3. **Matrix rain** — canvas or Pillow-generated glyph columns at **≤ 10% opacity**. Ambience, never decoration that fights data.
4. **Blinking cursor** — `▊` after prompts and log tails.
5. **Box-drawing chrome** — `┌─ ─┐ │ └─ ─┘` for status blocks. ASCII, not emoji.

## Voice

- Precise, technical, confident, quiet. Short sentences.
- Numbers over adjectives. Every claim carries a reproduce command.
- Losses are published. Honesty is the brand.

## Do / Don't

| Do | Don't |
|---|---|
| `● HUNTING` | 🔥🩸☠️💀 emoji anywhere |
| `ORDER EXECUTED` | "KILL ORDER" |
| "walk-forward validated" | "guaranteed profit" |
| "out-of-sample AUC 0.466 — gate rejected the model" | hiding losing experiments |
| `GOLD//REAPER` | "WE ARE THE PYTHON HUNTERS" |
| amber/red for risk states | blood-red everything |
| mock-data demos, zero keys | screenshots of fake profits |
