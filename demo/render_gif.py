#!/usr/bin/env python3
"""
GOLD//REAPER :: deterministic .cast -> .gif renderer (no agg required)
======================================================================
Reads an asciinema v2 .cast recording and renders it into an animated GIF
with Pillow: monospace terminal grid, dark theme, outro card appended.

Deterministic output -> identical GIF for identical .cast input.

Run:  python demo/render_gif.py [--cast demo/gold-reaper-demo.cast]
                                [--out demo/gold-reaper-demo.gif]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")

# 16 classic xterm colors on the brand-dark background
BG = (10, 14, 15)
FG = (160, 173, 168)
PALETTE = [
    (10, 14, 15), (60, 60, 60), (255, 106, 106), (0, 255, 156),   # 0-3
    (0, 217, 255), (255, 191, 0), (122, 240, 180), (200, 200, 200),  # 4-7
    (90, 90, 90), (120, 140, 135), (255, 85, 85), (0, 200, 120),  # 8-11
    (80, 200, 255), (255, 210, 80), (0, 255, 156), (255, 255, 255),  # 12-15
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "C:/Windows/Fonts/consola.ttf",
              "/System/Library/Fonts/Menlo.ttc"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)  # type: ignore[return-value]
    return ImageFont.load_default()  # type: ignore[return-value]


def parse_cast(path: Path) -> tuple[list[list[str]], int, float]:
    """Return (frames as char-grids, cols, duration). Frame emitted ~10/s."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    width = int(header.get("width", 100))
    height = int(header.get("height", 30))
    events = [(float(e[0]), e[2]) for e in
              (json.loads(ln) for ln in lines[1:] if ln.strip())
              if e[1] == "o" and len(e) > 2]
    rows = [[" " for _ in range(width)] for _ in range(height)]
    frames: list[list[list[str]]] = []
    cur = [r[:] for r in rows]
    cx = cy = 0
    last_t = 0.0
    step = 0.10                      # 10 fps target
    next_snap = step
    for t, chunk in events:
        last_t = t
        for ch in _ANSI.sub("", chunk):
            if ch == "\r":
                cx = 0
            elif ch == "\n":
                cx, cy = 0, min(cy + 1, height - 1)
            elif ch == "\x08":
                cx = max(0, cx - 1)
            elif ch == "\x1b":
                # consume simple CSI sequences (colors/moves): skip to letter
                pass
            elif ch >= " ":
                if cy < height and cx < width:
                    cur[cy][cx] = ch
                cx = min(cx + 1, width - 1)
        if t >= next_snap:
            frames.append([r[:] for r in cur])
            next_snap += step
    frames.append([r[:] for r in cur])
    return frames, width, last_t  # type: ignore[return-value]


def render_gif(frames: list, width: int, out: Path,
               fps: int = 10, scale: int = 2) -> None:
    cw, ch_h = 8 * scale, 17 * scale
    img_w, img_h = width * cw + 32, 32 * ch_h + 32
    font = _font(14 * scale)
    imgs: list[Image.Image] = []
    for grid in frames[-360:]:            # cap ~36s at 10fps
        im = Image.new("RGB", (img_w, img_h), BG)
        d = ImageDraw.Draw(im)
        for y, row in enumerate(grid[:32]):
            d.text((16, 16 + y * ch_h), "".join(row), font=font, fill=FG)
        imgs.append(im)
    if not imgs:
        raise SystemExit("[render_gif] no frames decoded from cast")
    # outro card
    outro = Image.new("RGB", (img_w, img_h), BG)
    d = ImageDraw.Draw(outro)
    big = _font(24 * scale)
    small = _font(13 * scale)
    d.rectangle([8, 8, img_w - 8, img_h - 8], outline=PALETTE[3], width=2)
    d.text((img_w // 2 - 180 * scale, img_h // 2 - 30 * scale),
           "GOLD//REAPER", font=big, fill=PALETTE[3])
    d.text((img_w // 2 - 150 * scale, img_h // 2 + 14 * scale),
           "github.com/muhammadwhizz-web/gold-reaper",
           font=small, fill=PALETTE[5])
    imgs.append(outro)
    imgs[0].save(out, save_all=True, append_images=imgs[1:],
                 duration=int(1000 / fps), loop=0, optimize=True)
    print(f"[render_gif] wrote {out} ({out.stat().st_size:,} bytes, "
          f"{len(imgs)} frames, {width} cols)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cast", default=str(ROOT / "demo/gold-reaper-demo.cast"))
    ap.add_argument("--out", default=str(ROOT / "demo/gold-reaper-demo.gif"))
    args = ap.parse_args()
    frames, width, _dur = parse_cast(Path(args.cast))
    render_gif(frames, width, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
