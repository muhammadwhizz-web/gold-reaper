"""
GOLD//REAPER :: Logger — quiet terminal, rich when available.
=============================================================
Console output uses Rich (dark theme, green/cyan accents, rounded panels,
progress bars, sparklines) with a plain-text fallback. File logging is
unchanged: silent, rotating, utf-8.

Style contract (docs/BRAND.md): no skulls, no blood-red, no hype. LEDs and
thin frames only.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"

try:  # Rich is optional at runtime; requirements.txt ships it
    from rich import box
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
    try:
        from rich.sparkline import Sparkline  # rich >= 13 only
    except ImportError:                    # pragma: no cover
        Sparkline = None                   # type: ignore[assignment]
    RICH = True
    _THEME = Theme({
        "reaper.green": "#00FF9C", "reaper.cyan": "#00D9FF",
        "reaper.dim": "#5E6A66", "reaper.amber": "#FFBF00",
        "reaper.red": "#FF5555",
    })
    _console = Console(theme=_THEME)
except ImportError:  # pragma: no cover - CI installs rich; fallback safety
    RICH = False
    Panel = Progress = Sparkline = Table = Text = None  # type: ignore
    _console = None  # type: ignore


def _supports_color() -> bool:
    import sys
    if __import__("os").name == "nt":
        try:
            import colorama  # noqa: F401
            return True
        except ImportError:
            return False
    return sys.stdout.isatty()


def setup_logger(log_file: Path, name: str = "reaper") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                            "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=30,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)

    if RICH:
        ch: logging.Handler = RichHandler(console=_console, show_time=True,
                                          show_path=False, rich_tracebacks=True,
                                          markup=False,
                                          log_time_format="%H:%M:%S")
        ch.setFormatter(logging.Formatter("%(message)s"))
    else:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
    log.addHandler(ch)
    log.propagate = False
    return log


def cprint(text: str, color: str = RESET) -> None:
    if RICH:
        style = {RED: "reaper.red", GREEN: "reaper.green",
                 YELLOW: "reaper.amber", CYAN: "reaper.cyan",
                 GRAY: "reaper.dim"}.get(color)
        if style:
            _console.print(text, style=style)
            return
    if _supports_color():
        print(f"{color}{text}{RESET}", flush=True)
    else:
        print(text, flush=True)


# ─────────────────────────────────────────────────────────── rich helpers

def banner() -> str:
    """Session header — thin frame, terminal green, no skulls."""
    return ("GOLD//REAPER :: xau/usd autonomous hunter")


def print_banner(version: str = "") -> None:
    if not RICH:
        cprint(banner(), GREEN)
        return
    sub = "xau/usd autonomous hunter" + (f" · v{version}" if version else "")
    _console.print(Panel(
        Text.assemble(("GOLD//REAPER\n", "bold reaper.green"),
                      (sub, "reaper.dim")),
        border_style="reaper.green", box=box.ROUNDED, padding=(0, 2),
        title="reaper", title_align="left"))


def session_panel(state: dict) -> None:
    """
    Live status panel. Expected keys (all optional, defaults shown):
      session, status, equity, day_pnl_pct, open_side, open_px, open_tp,
      guard_daily, guard_block, streak, mode
    """
    if not RICH:
        cprint(str(state), GREEN)
        return
    eq = state.get("equity", 0.0)
    pnl = state.get("day_pnl_pct", 0.0)
    pnl_txt = f"{pnl:+.2f}% today"
    open_side = state.get("open_side")
    open_line = ("-" if not open_side else
                 f"{open_side} {state.get('open_px', 0):,.2f} → TP "
                 f"{state.get('open_tp', 0):,.2f}")
    body = Text()
    body.append("session   ", style="reaper.dim")
    body.append(f"{state.get('session', 'london/ny overlap')}")
    body.append(f"          {state.get('status', '● HUNTING')}\n",
                style="reaper.green")
    body.append("equity    ", style="reaper.dim")
    body.append(f"{eq:,.2f} USD".rjust(24))
    body.append(f"   {pnl_txt}\n",
                style="reaper.green" if pnl >= 0 else "reaper.red")
    body.append("open      ", style="reaper.dim")
    body.append(f"{open_line}\n")
    body.append("guard     ", style="reaper.dim")
    body.append(f"daily {state.get('guard_daily', '-3%')} · "
                f"block {state.get('guard_block', '+$20')} · "
                f"streak {state.get('streak', 0)}\n")
    body.append("mode      ", style="reaper.dim")
    body.append(f"{state.get('mode', 'PAPER')}", style="reaper.cyan")
    _console.print(Panel(body, border_style="reaper.green", box=box.ROUNDED,
                         title=f"reaper · "
                               f"{state.get('utc', 'UTC')}",
                         title_align="right", padding=(0, 1)))


def equity_sparkline(values: list[float], width: int = 48) -> None:
    """Print a one-line equity sparkline (Rich Sparkline or block chars)."""
    if not values:
        return
    if RICH and Sparkline is not None:
        _console.print(Sparkline(values[-width:], max_width=width))
        return
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    lo, hi = min(values[-width:]), max(values[-width:])
    span = (hi - lo) or 1.0
    line = "".join(blocks[min(7, int((v - lo) / span * 7.999))] for v in values[-width:])
    cprint(f"equity {line} {lo:,.0f}..{hi:,.0f}", GREEN)


def trades_table(trades: list[dict]) -> None:
    """Live table for open/recent trades. Keys: side, entry, sl, tp, pnl, regime."""
    if not RICH:
        for t in trades:
            cprint(str(t))
        return
    tb = Table(box=box.SIMPLE, border_style="reaper.dim", header_style="reaper.cyan")
    for col in ("side", "entry", "sl", "tp", "pnl", "regime"):
        tb.add_column(col)
    for t in trades:
        pnl = t.get("pnl", 0)
        tb.add_row(str(t.get("side", "-")), f"{t.get('entry', 0):,.2f}",
                   f"{t.get('sl', 0):,.2f}", f"{t.get('tp', 0):,.2f}",
                   Text(f"{pnl:+.2f}",
                        style="reaper.green" if pnl >= 0 else "reaper.red"),
                   str(t.get("regime", "-")))
    _console.print(tb)


def progress(description: str):
    """Progress bar context manager for backtests / ingest runs."""
    if not RICH:
        import contextlib

        @contextlib.contextmanager
        def _dummy():
            print(f"[{description}] ...")
            yield None
        return _dummy()
    return Progress(TextColumn(f"[reaper.green]{description}"),
                    BarColumn(bar_width=40),
                    MofNCompleteColumn(), TimeElapsedColumn(),
                    console=_console)
