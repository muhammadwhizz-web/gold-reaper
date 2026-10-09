#!/usr/bin/env python3
"""
GOLD//REAPER :: Social preview generator (1280x640)
====================================================
Renders assets/social-preview.png for the repo Settings -> Social preview:

  near-black #0A0E0F canvas · subtle matrix rain (deterministic)
  thin terminal-green box-drawing frame · mono title · live status strip

Deterministic output (seeded) so re-runs produce an identical file.
Font: JetBrains Mono when present, DejaVu Sans Mono fallback (metric-similar).

Run:  python assets/make_social.py [--out assets/social-preview.png]
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (10, 14, 15)            # #0A0E0F
GREEN = (0, 255, 156)        # #00FF9C
CYAN = (0, 217, 255)         # #00D9FF
AMBER = (255, 191, 0)
GRAY = (110, 122, 118)
RAIN_GREEN = (0, 255, 156)

FONT_CANDIDATES = [
    "JetBrainsMono-Bold.ttf", "JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        if bold and "Bold" not in name and not name.startswith("JetBrainsMono-Bold"):
            continue
        p = Path(name)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()  # type: ignore[return-value]


def matrix_rain(size: int = 14, opacity: int = 26) -> Image.Image:
    """Deterministic katakana rain layer, very low opacity."""
    rng = random.Random(666)
    layer = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(layer)
    cols = W // size
    trail = rng.randint(6, 14)
    for ci in range(cols):
        head = rng.randint(-trail * 3, H // size)
        for k in range(trail):
            y = (head - k) * size
            if y < -size or y > H:
                continue
            ch = chr(rng.choice(range(0x30A0, 0x30FF)))
            fade = max(0.12, 1.0 - k / trail)
            alpha = int(opacity * fade)
            col = tuple(min(255, int(c * alpha)) for c in RAIN_GREEN)
            d.text((ci * size + 2, y + 2), ch, font=load_font(size - 2, bold=False),
                   fill=col)
    return layer


def frame(d: ImageDraw.ImageDraw, pad: int = 36) -> None:
    """Thin terminal box-drawing frame."""
    x0, y0, x1, y1 = pad, pad, W - pad, H - pad
    d.rectangle([x0, y0, x1, y1], outline=GREEN, width=2)
    # corner ticks
    t = 18
    for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                           (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([cx, cy, cx + dx * t, cy], fill=GREEN, width=4)
        d.line([cx, cy, cx, cy + dy * t], fill=GREEN, width=4)
    # title-tab notch on the frame
    d.rectangle([x0, y0, x0 + 220, y0 + 34], fill=BG, outline=GREEN, width=2)
    d.text((x0 + 12, y0 + 8), "reaper@xauusd:~$", font=load_font(18, bold=False),
           fill=GREEN)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "social-preview.png"))
    args = ap.parse_args()

    img = matrix_rain().convert("RGB")
    d = ImageDraw.Draw(img)
    frame(d)

    f_title = load_font(92)
    f_sub = load_font(26, bold=False)
    f_status = load_font(24, bold=False)
    f_footer = load_font(18, bold=False)

    title = "GOLD//REAPER"
    tw = d.textlength(title, font=f_title)
    d.text(((W - tw) / 2, 176), title, font=f_title, fill=GREEN)

    sub = "autonomous xau/usd trading system"
    sw = d.textlength(sub, font=f_sub)
    d.text(((W - sw) / 2, 292), sub, font=f_sub, fill=(160, 173, 168))

    # status strip
    strip = "● HUNTING   ·   12:04 UTC   ·   +0.42%"
    stw = d.textlength(strip, font=f_status)
    sx = (W - stw) / 2
    d.rounded_rectangle([sx - 22, 360, sx + stw + 22, 408], radius=8,
                        outline=(0, 90, 60), width=1)
    d.text((sx, 368), strip, font=f_status, fill=GREEN)

    footer = "walk-forward validated  ·  142 features  ·  10 dimensions"
    fw = d.textlength(footer, font=f_footer)
    d.text(((W - fw) / 2, H - 78), footer, font=f_footer, fill=GRAY)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"[make_social] wrote {out} ({out.stat().st_size:,} bytes) "
          f"1280x640 deterministic seed=666")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
