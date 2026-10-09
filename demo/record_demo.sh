#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD//REAPER :: demo recording pipeline
#  asciinema cast -> GIF (agg) -> MP4 (ffmpeg)
#
#  outputs:
#    demo/gold-reaper-demo.cast   (asciinema - embeddable on GitHub)
#    demo/gold-reaper-demo.gif    (README embed)
#    demo/gold-reaper-demo.mp4    (YouTube / X upload)
#
#  requirements:
#    pip install asciinema
#    agg   -> cargo install agg   |  https://github.com/asciinema/agg/releases
#    ffmpeg (system package)
# ═══════════════════════════════════════════════════════════════
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

CAST="gold-reaper-demo.cast"
GIF="gold-reaper-demo.gif"
MP4="gold-reaper-demo.mp4"

have() { command -v "$1" >/dev/null 2>&1; }

for tool in asciinema agg ffmpeg; do
  if ! have "$tool"; then
    echo "[!] missing: $tool"
    case "$tool" in
      asciinema) echo "    install: pip install asciinema" ;;
      agg)       echo "    install: cargo install agg  (or grab a release binary:";
                 echo "             https://github.com/asciinema/agg/releases)" ;;
      ffmpeg)    echo "    install: apt install ffmpeg | brew install ffmpeg" ;;
    esac
    MISSING=1
  fi
done
if [ -n "${MISSING:-}" ]; then
  echo "[i] install the missing tools and re-run: bash demo/record_demo.sh"
  exit 1
fi

echo "[1/3] recording terminal demo (asciinema)..."
if have agg; then
  asciinema rec - overwrite "$CAST" -c "python3 $DIR/terminal_demo.py"
else
  # legacy asciinema versions use -w/-y instead of - overwrite
  asciinema rec -y "$CAST" -c "python3 $DIR/terminal_demo.py" || true
fi

echo "[2/3] rendering GIF..."
agg --speed 1 --theme dracula --font-family "JetBrains Mono,Monospace" \
    --font-size 14 --cols 110 --rows 32 "$CAST" "$GIF"

if have ffmpeg; then
  echo "[3/3] rendering MP4..."
  # pad to 1920x1080 for clean YouTube/X delivery, subtle upscale
  ffmpeg -y -i "$GIF" -vf "scale=1920:1080:flags=lanczos,setsar=1" \
         -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart "$MP4"
  echo "      -> $MP4 (upload to YouTube/X, then paste the link in README)"
else
  echo "[3/3] ffmpeg missing - skipping MP4"
fi

echo
echo "done. outputs:"
echo "  $DIR/$CAST"
echo "  $DIR/$GIF"
[ -f "$DIR/$MP4" ] && echo "  $DIR/$MP4"
echo
echo "NOTE: chat tools cannot host video. Record locally, upload the MP4 to"
echo "YouTube/X, then add the link to README.md under 'WATCH THE DEMO'."
