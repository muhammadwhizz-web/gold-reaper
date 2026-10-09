#!/usr/bin/env python3
"""
GOLD//REAPER :: Interactive Terminal Demo
=========================================
A self-contained simulation of the reaper's session. No broker, no keys,
no network - seeded synthetic feed only. Renders:

  1. kernel-style boot sequence
  2. 2s matrix rain overture
  3. live hunt stream: candle scans, module votes, a filled order,
     protective levels, equity drift
  4. session summary block

Run:        python demo/terminal_demo.py
Re-record:  bash demo/record_demo.sh
Deps:       pip install rich colorama
"""
from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timezone

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("missing deps:  pip install rich colorama")
    sys.exit(1)

try:
    import colorama  # noqa: F401  (Windows ANSI enablement)
    colorama.just_fix_windows_console()
except Exception:  # noqa: BLE001
    pass

GREEN = "#00FF9C"
CYAN = "#00D9FF"
AMBER = "#FFB000"
RED = "#FF3B3B"
MUTED = "#5A6B6F"
TEXT = "#C9D1D9"

console = Console()
GLYPHS = "01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾌﾍﾎ+=<>[]{}/\\|$#@%&*"


# ─────────────────────────────────────────────────────────── matrix rain

class MatrixRain:
    """Low-opacity falling glyph field rendered as a rich Text grid."""

    def __init__(self, width: int, height: int, rng: random.Random) -> None:
        self.w, self.h = width, height
        self.rng = rng
        self.heads = [rng.randint(0, height) for _ in range(width // 2)]
        self.speeds = [rng.randint(1, 2) for _ in range(width // 2)]

    def frame(self) -> Text:
        grid = [[" "] * self.w for _ in range(self.h)]
        colors = [["" for _ in range(self.w)] for _ in range(self.h)]
        for ci, head in enumerate(self.heads):
            x = ci * 2
            trail = self.rng.randint(4, 9)
            for k in range(trail):
                y = head - k
                if 0 <= y < self.h:
                    grid[y][x] = self.rng.choice(GLYPHS)
                    colors[y][x] = GREEN if k == 0 else "#0f5c3c"
            self.heads[ci] = (head + self.speeds[ci]) % (self.h + trail)
        out = Text()
        for r in range(self.h):
            for c in range(self.w):
                ch = grid[r][c]
                if ch == " ":
                    out.append(" ")
                else:
                    out.append(ch, style=colors[r][c])
            out.append("\n")
        return out


def matrix_overture(seconds: float = 2.0, seed: int = 666) -> None:
    rng = random.Random(seed)
    w = max(40, min(console.width - 2, 110))
    h = max(12, min(console.height - 4, 26))
    rain = MatrixRain(w, h, rng)
    t0 = time.time()
    with Live(rain.frame(), console=console, refresh_per_second=18,
              transient=True, screen=True):
        while time.time() - t0 < seconds:
            time.sleep(0.055)
    console.print(Text("G O L D / / R E A P E R", style=f"bold {GREEN}", justify="center"))
    console.print(Text("autonomous XAU/USD trading system", style=MUTED, justify="center"))


# ─────────────────────────────────────────────────────────── boot sequence

def boot_line(ok: bool, label: str, detail: str) -> None:
    mark = Text("[ ok ]", style=GREEN) if ok else Text("[ .. ]", style=AMBER)
    console.print(f"  {mark} {label:<28}", end="")
    console.print(Text(detail, style=MUTED))


def boot_sequence() -> None:
    console.print()
    console.print(Text("┌─ reaper init ─────────────────────────────────────┐",
                       style=MUTED))
    steps = [
        ("kernel/ansi terminal", "detected · 256color · monospace", True),
        ("market data layer", "13,722 h1 bars · 5,032 d1 bars · duckdb", True),
        ("feature forge", "142 features · 10 dimensions · 98.1% cov", True),
        ("regime engine", "hmm(5) fitted · rule fallback armed", True),
        ("ensemble APEX-X", "trend/meanrev/breakout/news modules online", True),
        ("meta-model", "soft vote (oos gate: auc 0.466 < 0.56 → vetoed)", True),
        ("risk engine", "block +$20/4h · day -3% · week -7% · month -15%", True),
        ("broker link", "paper simulator · spread $0.35 · slippage $0.05", True),
        ("audit trail", "data/audit.jsonl · append-only", True),
    ]
    for label, detail, ok in steps:
        boot_line(ok, label, detail)
        time.sleep(0.16)
    console.print(Text("└───────────────────────────────────────────────────┘",
                       style=MUTED))
    console.print()


# ─────────────────────────────────────────────────────────── hunt stream

def status_line(session: str, equity: float, day_pnl: float,
                pos: str | None) -> Text:
    t = Text()
    t.append(f" {datetime.now(timezone.utc):%H:%M:%S} UTC ", style=f"bold {MUTED}")
    t.append("│ ", style=MUTED)
    t.append(f"session {session:<17}", style=TEXT)
    t.append("● HUNTING", style=f"bold {GREEN}")
    t.append(f"   equity {equity:>10,.2f} USD ", style=TEXT)
    t.append(f"{day_pnl:+.2f} today", style=GREEN if day_pnl >= 0 else RED)
    if pos:
        t.append(f"   open {pos}", style=CYAN)
    return t


def hunt(seed: int = 666) -> None:
    px = 2_412.50
    equity = 10_000.00
    day_pnl = 0.0
    session = "london/ny overlap"
    a = 3.6

    console.print(Text("the hunt (simulated feed - no broker, no keys)",
                       style=MUTED))
    console.print()

    scans = [
        ("bar 2,415.80  atr 3.4  adx 27  regime TREND_UP",
         "trend: pullback to ema20 · rsi 44.8 reset · waiting trigger"),
        ("bar 2,414.10  atr 3.5  adx 26  regime TREND_UP",
         "trend: within 0.6x atr of ema20 · rsi 41.2 · trigger pending"),
        ("bar 2,413.90  atr 3.6  adx 27  regime TREND_UP",
         "trend: bullish body · rsi 43.9 turning · ensemble 2/2 · ARming"),
        ("bar 2,418.60  atr 3.6  adx 28  regime TREND_UP",
         "meanrev: silent (bands calm) · breakout: silent (no expansion)"),
        ("bar 2,420.15  atr 3.7  adx 28  regime TREND_UP",
         "trend: position riding · breakeven armed at +1R"),
    ]
    for i, (bar, note) in enumerate(scans):
        console.print(Text(f"  scan {i + 1:>2} ", style=MUTED), end="")
        console.print(Text(bar, style=TEXT))
        console.print(Text(f"        └─ {note}", style=MUTED))
        time.sleep(0.32)

    # the order
    entry = round(px + 6.1, 2)
    sl = round(entry - 1.2 * a, 2)
    tp = round(entry + 3.2 * a, 2)
    oz = round((equity * 0.01) / (entry - sl), 3)  # noqa: F841 (shown in summary)
    console.print()
    order = Table(box=box.SIMPLE_HEAVY, show_header=False, pad_edge=False,
                  width=74)
    order.add_column(style=TEXT)
    order.add_row(Text("ORDER EXECUTED", style=f"bold {GREEN}"),
                  Text("XAUUSD · LONG · 0.028 oz", style=TEXT))
    order.add_row("entry", f"{entry:,.2f}")
    order.add_row("stop", f"{sl:,.2f}   (1.2 x atr)", style=MUTED)
    order.add_row("target", f"{tp:,.2f}   (3.2 x atr · r:r 2.67)", style=MUTED)
    order.add_row("reasoning", "h4 bias up · h1 pullback reset · adx 28 · "
                               "ensemble 2.5/4.0", style=MUTED)
    console.print(Panel(order, border_style=GREEN, title="reaper · fill",
                        title_align="left", width=80))
    time.sleep(0.5)

    fills = [
        ("bar 2,419.4", "protective move: stop -> breakeven 2,417.90", GREEN),
        ("bar 2,423.1", "trailing engaged at +1.5R · trail 1.2 x atr", CYAN),
        ("bar 2,431.2", "TARGET FILLED 2,431.20 · pnl +$23.41 (+0.23%)", GREEN),
    ]
    for bar, msg, col in fills:
        console.print(f"  {Text(bar, style=MUTED)}  {Text(msg, style=col)}")
        time.sleep(0.4)
        if "TARGET" in msg:
            day_pnl += 23.41
            equity += 23.41
        console.print(status_line(session, equity, day_pnl, None))
        time.sleep(0.2)


def summary(equity: float, day_pnl: float) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=MUTED, justify="right")
    grid.add_column(style=TEXT)
    rows = [
        ("session", "london/ny overlap · 12:00-16:00 UTC"),
        ("blocks", "1 / 1 banked (target +$20.00)"),
        ("day pnl", f"{day_pnl:+.2f} USD  (+{day_pnl / equity * 100:.2f}%)"),
        ("win rate", "100.0%  (1W / 0L)"),
        ("guard", "daily -3% · weekly -7% · monthly -15%  · LATCHED-ON-BREACH"),
        ("next", "cooldown · block budget spent · standby ●"),
    ]
    for k, v in rows:
        grid.add_row(k, v)
    console.print()
    console.print(Panel(grid, title="session summary", title_align="left",
                        border_style=GREEN, width=80))
    console.print(Text("paper simulation · past performance does not guarantee "
                       "future results", style=MUTED))
    console.print(Text("docs: docs/APEX.md · architecture, strategy, risk",
                       style=MUTED))
    console.print()


def main() -> int:
    matrix_overture(2.0)
    boot_sequence()
    hunt()
    summary(10_023.41, 23.41)
    return 0


if __name__ == "__main__":
    sys.exit(main())
