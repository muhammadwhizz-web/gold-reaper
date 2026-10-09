#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: macOS .dmg builder (used locally + release.yml)
#  output: installer/output/GoldReaper-<version>.dmg
#  usage:  ./installer/build_dmg.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"
VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/version *= *"\(.*\)"/\1/')"
OUT="installer/output"
STAGE="$(mktemp -d)/GoldReaper-dmg"

echo "██ building GoldReaper-$VERSION.dmg"
mkdir -p "$STAGE/$OUT" "$STAGE/cont"

# 1. .app bundle with real payload
APP="$STAGE/cont/Gold Reaper.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
rsync -a --exclude '.git' --exclude '.venv' --exclude 'venv' \
      --exclude '__pycache__' --exclude '*.pyc' --exclude 'data' \
      --exclude '.env' --exclude 'logs' --exclude 'installer' \
      "$SRC"/ "$APP/Contents/app"/
cp -f assets/icon.icns "$APP/Contents/Resources/icon.icns"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>Gold Reaper</string>
    <key>CFBundleDisplayName</key>       <string>Gold Reaper</string>
    <key>CFBundleIdentifier</key>        <string>com.goldreaper.app</string>
    <key>CFBundleVersion</key>           <string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleExecutable</key>        <string>gold-reaper</string>
    <key>CFBundleIconFile</key>          <string>icon</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>LSMinimumSystemVersion</key>    <string>12.0</string>
    <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
EOF
# launcher: copies payload to user share, creates venv, starts bot
cat > "$APP/Contents/MacOS/gold-reaper" <<'LAUNCH'
#!/bin/zsh
set -e
APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"
SHARE="$HOME/.local/share/gold-reaper"
mkdir -p "$SHARE/logs"
if [ ! -d "$SHARE/venv" ]; then
  echo "[i] first launch: preparing runtime (venv + deps)..."
  rsync -a --exclude '__pycache__' "$APP_DIR"/ "$SHARE/app"/
  python3 -m venv "$SHARE/venv"
  "$SHARE/venv/bin/pip" install -q --upgrade pip
  "$SHARE/venv/bin/pip" install -q -r "$SHARE/app/requirements.txt"
fi
cd "$SHARE/app"
if [ ! -f .env ]; then "$SHARE/venv/bin/python" -m setup.wizard; fi
nohup "$SHARE/venv/bin/python" dashboard/app.py >> "$SHARE/logs/dashboard.log" 2>&1 &
exec "$SHARE/venv/bin/python" bot.py
LAUNCH
chmod +x "$APP/Contents/MacOS/gold-reaper"

# 2. Applications symlink + README
ln -s /Applications "$STAGE/cont/Applications"
cat > "$STAGE/cont/README.txt" <<'EOF'
GOLD REAPER
===========
1. drag "Gold Reaper" into Applications
2. open it from Applications (first launch: right-click -> Open
   to pass Gatekeeper; it prepares the runtime, then the setup
   wizard asks for broker + risk settings)
3. dashboard: http://localhost:8080
4. docs: https://github.com/muhammadwhizz-web/gold-reaper

Paper mode is the default. No bot guarantees profit.
EOF

# 3. dmg
rm -rf "$OUT"; mkdir -p "$OUT"
hdiutil create -volname "Gold Reaper" -srcfolder "$STAGE/cont" \
  -ov -format UDZO "$OUT/GoldReaper-$VERSION.dmg"
echo "[OK] $OUT/GoldReaper-$VERSION.dmg"
