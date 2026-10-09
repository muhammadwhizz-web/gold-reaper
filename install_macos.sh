#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: macOS installer (bonus tier)
#═════════════════════════════════════════════════════════════════
#  1. Homebrew-based python 3.11 install if missing
#  2. app copy -> ~/.local/share/gold-reaper/app + venv
#  3. /Applications/Gold Reaper.app (icon.icns, double-clickable)
#  4. LaunchAgent com.goldreaper.bot (KeepAlive, RunAtLoad) -> 24/7
#  5. first-time setup wizard
#
#  usage:  chmod +x install_macos.sh && ./install_macos.sh
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; GRAY='\033[90m'; NC='\033[0m'
echo -e "${RED}██ gold-reaper :: macOS installer${NC}"

SRC="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/gold-reaper"
APPDIR="$SHARE/app"
VENV="$SHARE/venv"
APPBUNDLE="/Applications/Gold Reaper.app"
PLIST_DIR="$HOME/Library/LaunchAgents"

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
step() { echo -e "${YEL}[i]${NC} $1"; }
die()  { echo -e "${RED}[!]${NC} $1"; exit 1; }

[ "$(uname)" = "Darwin" ] || die "this script is for macOS (use install_linux.sh on Linux)"

# ---- 1. homebrew + python ------------------------------------------------
if ! command -v brew >/dev/null; then
  step "Homebrew missing -> installing (needs your password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || die "brew install failed - install Homebrew from https://brew.sh and re-run"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi
if ! command -v python3 >/dev/null; then
  step "installing python via brew..."
  brew install python@3.12
fi
v="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
ok "python $v"

# ---- 2. app copy + venv ----------------------------------------------------
step "copying app -> $APPDIR"
mkdir -p "$SHARE" "$APPDIR" "$SHARE/logs"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude 'venv' \
  --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'data' --exclude '.env' --exclude 'logs' \
  "$SRC"/ "$APPDIR"/
mkdir -p "$APPDIR/data"
ln -sfn "app/data/reaper.log" "$SHARE/logs/reaper.log"

step "creating venv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
step "installing requirements..."
"$VENV/bin/pip" install -r "$APPDIR/requirements.txt" -q
ok "APEX stack installed (MetaTrader5 auto-skipped on macOS)"

# ---- 3. wizard ---------------------------------------------------------------
if [ ! -f "$APPDIR/.env" ]; then
  step "first-time setup wizard"
  (cd "$APPDIR" && "$VENV/bin/python" -m setup.wizard) || true
else
  ok ".env preserved"
fi

# ---- 4. .app bundle ------------------------------------------------------------
step "creating $APPBUNDLE"
mkdir -p "$APPBUNDLE/Contents/MacOS" "$APPBUNDLE/Contents/Resources"
cp -f "$SRC/assets/icon.icns" "$APPBUNDLE/Contents/Resources/icon.icns" 2>/dev/null \
  || cp -f "$APPDIR/assets/icon.icns" "$APPBUNDLE/Contents/Resources/icon.icns"
cp -f "$SRC/start.command" "$APPBUNDLE/Contents/MacOS/gold-reaper" 2>/dev/null \
  || cp -f "$APPDIR/start.command" "$APPBUNDLE/Contents/MacOS/gold-reaper"
chmod +x "$APPBUNDLE/Contents/MacOS/gold-reaper"

cat > "$APPBUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>Gold Reaper</string>
    <key>CFBundleDisplayName</key>       <string>Gold Reaper</string>
    <key>CFBundleIdentifier</key>        <string>com.goldreaper.app</string>
    <key>CFBundleVersion</key>           <string>2.2.0</string>
    <key>CFBundleShortVersionString</key><string>2.2.0</string>
    <key>CFBundleExecutable</key>        <string>gold-reaper</string>
    <key>CFBundleIconFile</key>          <string>icon</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>LSMinimumSystemVersion</key>    <string>12.0</string>
    <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
EOF
ok ".app bundle ready (Dock icon while running)"

# ---- 5. LaunchAgent (24/7) ------------------------------------------------------
step "registering LaunchAgent com.goldreaper.bot"
mkdir -p "$PLIST_DIR"
cat > "$PLIST_DIR/com.goldreaper.bot.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>          <string>com.goldreaper.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python</string>
        <string>$APPDIR/bot.py</string>
    </array>
    <key>WorkingDirectory</key> <string>$APPDIR</string>
    <key>RunAtLoad</key>       <true/>
    <key>KeepAlive</key>       <true/>
    <key>ThrottleInterval</key> <integer>30</integer>
    <key>StandardOutPath</key> <string>$SHARE/logs/launchagent.log</string>
    <key>StandardErrorPath</key><string>$SHARE/logs/launchagent.err.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST_DIR/com.goldreaper.bot.plist" 2>/dev/null || true
launchctl load "$PLIST_DIR/com.goldreaper.bot.plist"
ok "LaunchAgent loaded (starts at login, restarts if it dies)"

# ---- 6. success ------------------------------------------------------------------
echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} GOLD REAPER INSTALLED${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  app bundle : $APPBUNDLE (double-click to launch + dashboard)"
echo "  app        : $APPDIR"
echo "  dashboard  : http://localhost:8080"
echo "  bot log    : $APPDIR/data/reaper.log"
echo "  stop bot   : launchctl unload ~/Library/LaunchAgents/com.goldreaper.bot.plist"
echo "  uninstall  : see docs/TROUBLESHOOTING.md #13"
echo ""
echo -e "${RED}  RULE #1: PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${RED}  RULE #2: never risk money you cannot burn.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
