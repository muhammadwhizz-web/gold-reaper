#!/usr/bin/env python3
"""
GOLD//REAPER :: Brand Asset Generator (Pillow, deterministic)
=============================================================
Generates the two visual identities of the project:

  assets/social-preview.png   1280x640 - GitHub social preview spec
  docs/banner.png             1200x360 - README header banner

Design language (docs/BRAND.md):
  background #0A0E0F · primary #00FF9C · secondary #00D9FF
  muted #5A6B6F · text #C9D1D9 · danger #FF3B3B (unused here)
  thin 1px terminal frame · monospace (DejaVu Sans Mono) · subtle
  matrix glyph columns at low alpha · NO skulls, NO blood, NO hype.

Run:  python assets/gen_brand.py
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"
ASSETS.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)

BG = (10, 14, 15)
GREEN = (0, 255, 156)
CYAN = (0, 217, 255)
MUTED = (90, 107, 111)
TEXT = (201, 209, 217)
GRID = (21, 28, 30)

GLYPHS = "01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾌﾍﾎ-+=<>[]{}/\\|$#@%&*"


def _font(size: int, bold: bool = False):
    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default(size)


def _matrix_columns(img: Image.Image, rng: random.Random,
                    cols: int = 60, alpha: int = 26) -> None:
    """Scatter faint falling-glyph columns (the rain, at low alpha)."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    w, h = img.size
    step = max(8, w // cols)
    f = _font(11)
    for x in range(0, w, step):
        if rng.random() > 0.55:
            continue
        y = rng.randint(0, h)
        trail = rng.randint(6, 16)
        for k in range(trail):
            yy = y - k * 12
            if 0 <= yy < h:
                g = rng.choice(GLYPHS)
                a = max(4, int(alpha * (1 - k / trail)))
                d.text((x, yy), g, font=f, fill=(0, 255, 156, a))
    img.alpha_composite(overlay)


def _terminal_frame(d: ImageDraw.Draw, box: tuple, color=GREEN) -> None:
    x0, y0, x1, y1 = box
    d.rectangle([x0, y0, x1, y1], outline=color, width=1)
    t = 14
    for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                           (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([cx, cy, cx + dx * t, cy], fill=color, width=2)
        d.line([cx, cy, cx, cy + dy * t], fill=color, width=2)


def _subtle_grid(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    w, h = img.size
    for x in range(0, w, 48):
        d.line([x, 0, x, h], fill=GRID, width=1)
    for y in range(0, h, 48):
        d.line([0, y, w, y], fill=GRID, width=1)


def make_social() -> Path:
    rng = random.Random(666)
    img = Image.new("RGBA", (1280, 640), BG + (255,))
    _subtle_grid(img)
    _matrix_columns(img, rng, cols=106, alpha=30)
    d = ImageDraw.Draw(img)

    _terminal_frame(d, (36, 36, 1244, 604))
    d.text((70, 84), "GOLD//REAPER", font=_font(92, bold=True), fill=GREEN)
    d.text((72, 196), "autonomous xauusd hunter", font=_font(30), fill=TEXT)
    d.line([72, 258, 620, 258], fill=MUTED, width=1)

    status = [
        ("engine",   "APEX-X ensemble · 142 features · 10 dimensions", TEXT),
        ("guard",    "circuit breakers armed · day -3% · block +$20", TEXT),
        ("session",  "london/ny overlap", CYAN),
    ]
    y = 300
    for k, v, col in status:
        d.text((72, y), f"{k:<9}", font=_font(24), fill=MUTED)
        d.text((230, y), v, font=_font(24), fill=col)
        y += 46
    d.ellipse([72, y + 4, 84, y + 16], fill=GREEN)
    d.text((100, y), "HUNTING", font=_font(24, bold=True), fill=GREEN)
    d.text((1244 - 8, 566), "walk-forward validated", font=_font(18), fill=MUTED,
           anchor="ra")

    out = ASSETS / "social-preview.png"
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out


def make_banner() -> Path:
    rng = random.Random(1337)
    img = Image.new("RGBA", (1200, 360), BG + (255,))
    _subtle_grid(img)
    _matrix_columns(img, rng, cols=100, alpha=26)
    d = ImageDraw.Draw(img)

    _terminal_frame(d, (28, 28, 1172, 332))
    d.text((60, 66), "GOLD//REAPER", font=_font(64, bold=True), fill=GREEN)
    d.text((62, 148), "autonomous XAU/USD trading system", font=_font(24), fill=TEXT)

    bars = [
        ("data", "20y recon · 65k bars · 12 tf", TEXT),
        ("edge", "walk-forward verified", TEXT),
        ("guard", "breakers latched on breach", TEXT),
    ]
    y = 208
    for k, v, col in bars:
        d.text((62, y), f"{k:<7}", font=_font(20), fill=MUTED)
        d.text((180, y), v, font=_font(20), fill=col)
        y += 34
    d.ellipse([62, y + 3, 72, y + 13], fill=GREEN)
    d.text((84, y), "HUNTING", font=_font(20, bold=True), fill=GREEN)

    out = DOCS / "banner.png"
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out


def main() -> int:
    a, b = make_social(), make_banner()
    print(f"brand assets generated:\n  {a}\n  {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
