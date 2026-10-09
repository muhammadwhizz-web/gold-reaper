#!/usr/bin/env python3
"""
GOLD REAPER :: icon forge
=========================
Generates every icon the cross-platform launchers need, from one
deterministic master drawing — no fonts, no network, no randomness:

  icon.png   512x512   (Linux .desktop, dashboard, tray)
  icon.ico   16..256   (Windows shortcuts, Inno Setup)
  icon.icns  16..512   (macOS .app bundle)

Design contract (docs/BRAND.md):
  dark charcoal square #0A0E0F · thin terminal-green frame #00FF9C
  chunky terminal "GR" glyph · gold accent bar #D4AF37 · status LED

Usage:
  python assets/make_icon.py [--out assets]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# ── brand palette (docs/BRAND.md) ────────────────────────────────
BG = (10, 14, 15, 255)          # #0A0E0F dark charcoal
GREEN = (0, 255, 156, 255)      # #00FF9C terminal green
GOLD = (212, 175, 55, 255)      # #D4AF37 accent
GREEN_DIM = (0, 255, 156, 28)   # rain hint
LED_OFF = (60, 76, 70, 255)

SIZE = 512
CORNER = 64                     # rounded-square radius
FRAME_INSET = 26                # frame distance from edge
FRAME_W = 7                     # thin frame stroke

# ── 5x7 terminal pixel font: G and R ─────────────────────────────
GLYPH_G = [
    "01110",
    "10001",
    "10000",
    "10111",
    "10001",
    "10001",
    "01110",
]
GLYPH_R = [
    "11110",
    "10001",
    "10001",
    "11110",
    "10100",
    "10010",
    "10001",
]


def _draw_frame(d: ImageDraw.ImageDraw) -> None:
    """Thin terminal-green frame + faint matrix rain hint inside it."""
    x0 = y0 = FRAME_INSET
    x1 = y1 = SIZE - FRAME_INSET
    # faint rain columns (10% opacity) — deterministic pattern
    rng = 0
    for cx in range(x0 + 22, x1 - 16, 38):
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        seg = (rng >> 8) % 55 + 18
        d.rectangle([cx, y0 + 16, cx + 4, y0 + 16 + seg], fill=GREEN_DIM)
    d.rectangle([x0, y0, x1, y0 + FRAME_W], fill=GREEN)
    d.rectangle([x0, y1 - FRAME_W, x1, y1], fill=GREEN)
    d.rectangle([x0, y0, x0 + FRAME_W, y1], fill=GREEN)
    d.rectangle([x1 - FRAME_W, y0, x1, y1], fill=GREEN)
    # corner ticks — terminal crosshair accents
    t = 26
    for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                             (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.rectangle([min(cx, cx + dx * t), min(cy, cy + dy * 5),
                     max(cx, cx + dx * t), max(cy, cy + dy * 5)], fill=GREEN)
        d.rectangle([min(cx, cx + dx * 5), min(cy, cy + dy * t),
                     max(cx, cx + dx * 5), max(cy, cy + dy * t)], fill=GREEN)


def _draw_glyph(d: ImageDraw.ImageDraw) -> None:
    """Chunky terminal GR with scanline-dim rows for CRT feel."""
    grid = [GLYPH_G[r] + "0" + GLYPH_R[r] for r in range(7)]  # side by side
    cols, rows = 11, 7
    block, gap = 27, 5
    gw, gh = cols * (block + gap) - gap, rows * (block + gap) - gap
    ox, oy = (SIZE - gw) // 2, (SIZE - gh) // 2 - 14
    for r, row in enumerate(grid):
        y = oy + r * (block + gap)
        if (r % 2) == 1:                          # scanline: slightly dim rows
            fill = tuple(int(c * 0.82) for c in GREEN[:3]) + (255,)
        else:
            fill = GREEN
        for c, ch in enumerate(row):
            if ch != "1":
                continue
            x = ox + c * (block + gap)
            d.rectangle([x, y, x + block - 1, y + block - 1], fill=fill)


def _draw_extras(d: ImageDraw.ImageDraw) -> None:
    # gold bar under the glyph — the gold we hunt
    bx0, bx1 = SIZE // 2 - 96, SIZE // 2 + 96
    by = SIZE - FRAME_INSET - 78
    d.rectangle([bx0, by, bx1, by + 10], fill=GOLD)
    # status LED — top right, alive
    lx, ly = SIZE - FRAME_INSET - 42, FRAME_INSET + 42
    d.ellipse([lx - 13, ly - 13, lx + 13, ly + 13], fill=LED_OFF)
    d.ellipse([lx - 9, ly - 9, lx + 9, ly + 9], fill=GREEN)
    d.ellipse([lx - 4, ly - 6, lx + 2, ly], fill=(170, 255, 220, 255))


def master() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=CORNER, fill=BG)
    # subtle inner panel to separate rain from edge
    d.rounded_rectangle([FRAME_INSET - 10, FRAME_INSET - 10,
                         SIZE - FRAME_INSET + 9, SIZE - FRAME_INSET + 9],
                        radius=CORNER - 16, fill=(14, 19, 20, 255))
    _draw_frame(d)
    _draw_glyph(d)
    _draw_extras(d)
    # round the alpha corners again (frame draws over them otherwise)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1],
                                           radius=CORNER, fill=255)
    img.putalpha(mask)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description="forge gold-reaper icons")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent),
                    help="output directory (default: assets/)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    img = master()

    png = out / "icon.png"
    img.save(png, "PNG")
    print(f"[OK] {png}")

    ico = out / "icon.ico"
    img.save(ico, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    print(f"[OK] {ico}")

    icns = out / "icon.icns"
    img.save(icns, format="ICNS")   # Pillow embeds the standard size set
    print(f"[OK] {icns}")

    # tray-friendly small variant (status LED visible at 64px)
    tray = out / "icon-tray.png"
    img.resize((64, 64), Image.LANCZOS).save(tray, "PNG")
    print(f"[OK] {tray}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
