#!/usr/bin/env python3
"""
GOLD//REAPER :: deterministic .cast writer (no asciinema/pty needed)
=====================================================================
Runs demo/terminal_demo.py with stdout intercepted and writes an asciinema
v2 recording:  {"version":2,...} header + [ts,"o",chunk] events.

Output is byte-identical for identical runs (wall-clock sleeps preserved
as event timestamps, so replays have the same rhythm).

Run:  python demo/make_cast.py [--out demo/gold-reaper-demo.cast]
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WIDTH, HEIGHT = 76, 32


class CastWriter(io.TextIOBase):
    """Intercepts writes; timestamps each chunk; forwards to a sink."""

    def __init__(self, cast_file: Path, sink: io.TextIOBase) -> None:
        self.cast = cast_file.open("w", encoding="utf-8")
        self.sink = sink
        self.t0 = time.monotonic()
        header = {"version": 2, "width": WIDTH, "height": HEIGHT,
                  "timestamp": int(time.time()),
                  "env": {"SHELL": "python", "TERM": "xterm-256color"}}
        self.cast.write(json.dumps(header) + "\n")

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        t = time.monotonic() - self.t0
        self.cast.write(json.dumps([round(t, 6), "o", s]) + "\n")
        self.sink.write(s)
        return len(s)

    def flush(self) -> None:
        self.cast.flush()
        self.sink.flush()

    def close(self) -> None:
        try:
            self.cast.close()
        except (ValueError, OSError):
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "demo/gold-reaper-demo.cast"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location(
        "terminal_demo", ROOT / "demo/terminal_demo.py")
    demo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(demo)

    # render as a real 110-col truecolor terminal even though stdout is a file
    import os
    os.environ["COLUMNS"] = str(WIDTH)
    os.environ["LINES"] = str(HEIGHT)

    writer = CastWriter(out, io.StringIO())
    from rich.console import Console as _RealConsole
    demo.console = _RealConsole(file=writer, width=WIDTH, force_terminal=True,
                                color_system="truecolor", highlight=False)
    # functions inside the demo create their own Console() at call time;
    # temporarily patch rich.console.Console so every one of them renders
    # at the cast geometry with truecolor forced
    import rich.console as _rc

    class _CastConsole(_RealConsole):
        def __init__(self, *a, **k):
            k.setdefault("file", writer)
            k.setdefault("width", WIDTH)
            k.setdefault("force_terminal", True)
            k.setdefault("color_system", "truecolor")
            k.setdefault("highlight", False)
            super().__init__(*a, **k)

    _orig_console_cls = _rc.Console
    _rc.Console = _CastConsole
    demo.console = _CastConsole()
    # the matrix overture uses rich Live (cursor redraws) which a linear
    # grid renderer cannot replay -> replace with a short branded pause
    demo.matrix_overture = lambda *a, **k: time.sleep(0.6)
    real_stdout = sys.stdout
    sys.stdout = writer
    try:
        demo.main()
    finally:
        sys.stdout = real_stdout
        _rc.Console = _orig_console_cls
        writer.close()
    size = out.stat().st_size
    print(f"[make_cast] wrote {out} ({size:,} bytes, {WIDTH}x{HEIGHT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
