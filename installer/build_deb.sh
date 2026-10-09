#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: .deb builder (Ubuntu/Debian) - local + release.yml
#  output: installer/output/gold-reaper_<version>_all.deb
#
#  The deb ships the payload + desktop icon + /usr/bin/gold-reaper.
#  The wrapper creates a per-user venv on first run (no root pip),
#  so upgrades stay clean and uninstalls stay trivial.
#  usage:  ./installer/build_deb.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"
VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/version *= *"\(.*\)"/\1/')"
OUT="installer/output"
ROOT="installer/output/deb-root"

echo "██ building gold-reaper_${VERSION}_all.deb"
rm -rf "$ROOT"; mkdir -p \
  "$ROOT/DEBIAN" \
  "$ROOT/usr/lib/gold-reaper" \
  "$ROOT/usr/bin" \
  "$ROOT/usr/share/applications" \
  "$ROOT/usr/share/icons/hicolor/512x512/apps" \
  "$ROOT/usr/share/doc/gold-reaper"

# ---- payload ----
rsync -a --exclude '.git' --exclude '.venv' --exclude 'venv' \
      --exclude '__pycache__' --exclude '*.pyc' --exclude 'data' \
      --exclude '.env' --exclude 'logs' --exclude 'installer' \
      "$SRC"/ "$ROOT/usr/lib/gold-reaper/"
cp -f assets/icon.png "$ROOT/usr/share/icons/hicolor/512x512/apps/gold-reaper.png"
cp -f LICENSE "$ROOT/usr/share/doc/gold-reaper/copyright" 2>/dev/null || true
cp -f README.md docs/GETTING_STARTED.md docs/TROUBLESHOOTING.md \
   "$ROOT/usr/share/doc/gold-reaper/" 2>/dev/null || true

# ---- /usr/bin wrapper ----
cat > "$ROOT/usr/bin/gold-reaper" <<'EOF'
#!/usr/bin/env bash
# GOLD REAPER launcher: per-user runtime on first call, then start.sh
set -u
SHARE="$HOME/.local/share/gold-reaper"
PAYLOAD="/usr/lib/gold-reaper"
mkdir -p "$SHARE/logs"
if [ ! -x "$SHARE/venv/bin/python" ]; then
  echo "[i] first run: preparing runtime (venv + deps)..."
  mkdir -p "$SHARE/app"
  cp -r "$PAYLOAD"/. "$SHARE/app"/
  python3 -m venv "$SHARE/venv"
  "$SHARE/venv/bin/pip" install -q --upgrade pip
  "$SHARE/venv/bin/pip" install -q -r "$SHARE/app/requirements.txt"
  echo "[OK] runtime ready"
fi
exec "$SHARE/app/start.sh" "${1:-start}"
EOF
chmod 755 "$ROOT/usr/bin/gold-reaper"

# ---- desktop icon ----
cat > "$ROOT/usr/share/applications/gold-reaper.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Gold Reaper
Comment=Autonomous XAU/USD hunter
Exec=gold-reaper start
Icon=gold-reaper
Terminal=true
Categories=Office;Finance;
EOF

# ---- DEBIAN control + hooks ----
cat > "$ROOT/DEBIAN/control" <<EOF
Package: gold-reaper
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-venv, python3-pip
Recommends: rsync
Installed-Size: $(du -sk "$ROOT/usr" | cut -f1)
Maintainer: gold-reaper project <muhammadwhizz-web@users.noreply.github.com>
Homepage: https://github.com/muhammadwhizz-web/gold-reaper
Description: Autonomous XAU/USD trading hunter (Exness MT5 / Bitget / Paper)
 Multi-broker XAU/USD bot with regime-routed ensemble strategy,
 $20/4h block risk engine, latched circuit breakers, watchdog,
 health monitor, live dashboard. Paper mode is the default.
 No bot guarantees profit.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor || true
fi
cat <<MSG

GOLD REAPER installed.
  launch      : gold-reaper start      (or the desktop icon)
  dashboard   : http://localhost:8080
  24/7 setup  : install_linux.sh (registers systemd + linger)

Paper mode is the default. No bot guarantees profit.
MSG
EOF
chmod 755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
# stop user services if they were created from the deb's runtime copy
for u in gold-reaper gold-reaper-dashboard gold-reaper-watchdog; do
  systemctl --user stop "$u" 2>/dev/null || true
  systemctl --user disable "$u" 2>/dev/null || true
done
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/prerm"

# ---- build ----
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$ROOT" "$OUT/gold-reaper_${VERSION}_all.deb"
echo "[OK] $OUT/gold-reaper_${VERSION}_all.deb"
