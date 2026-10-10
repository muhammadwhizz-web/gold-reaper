#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: macOS one-command installer (P1-E repair build)
#═════════════════════════════════════════════════════════════════
#  Contract: brew python@3.12 (validated) -> venv at project root ->
#  pinned requirements.lock -> LaunchAgent with KeepAlive -> desktop
#  (.app) icon -> installer/health_check.py GATE: SUCCESS only on pass.
#  Idempotent; never overwrites .env.
#    ./install_macos.sh --uninstall [--purge]
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; GRAY='\033[90m'; NC='\033[0m'
echo -e "${RED}██ gold-reaper :: macOS installer (reliability build)${NC}"

SRC="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$HOME/Library/Application Support/GoldReaper"
VENV="$APPDIR/.venv"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.goldreaper.bot.plist"
APPS_DIR="$HOME/Applications"
ARCH="$(uname -m)"

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
step() { echo -e "${YEL}[i]${NC} $1"; }
warn() { echo -e "${YEL}[!]${NC} $1"; }
die()  { echo -e "${RED}[!] INSTALL FAILED: $1${NC}"; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  step "unloading + removing LaunchAgent"
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$APPS_DIR/Gold Reaper.app"
  rm -rf "$VENV"
  ok "agent, app bundle and venv removed"
  echo "  app dir   : $APPDIR (kept - data/ + .env preserved; --purge wipes)"
  [ "${2:-}" = "--purge" ] && { rm -rf "$APPDIR"; ok "PURGED $APPDIR"; }
  exit 0
fi

py_in_range() {
  [ -x "$1" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if (3,10)<=(sys.version_info[0],sys.version_info[1])<=(3,12) else 1)' 2>/dev/null
}

# ── 1. python via Homebrew (validated) ─────────────────────────
PYBIN=""
step "acquiring python 3.10-3.12 ($ARCH)"
for cand in "$(command -v python3.12 || true)" \
            "$(command -v python3.11 || true)" \
            "$(command -v python3.10 || true)" \
            "/opt/homebrew/bin/python3.12" "/usr/local/bin/python3.12" \
            "$(command -v python3 || true)"; do
  if py_in_range "$cand"; then PYBIN="$cand"; break; fi
done
if [ -z "$PYBIN" ] && command -v brew >/dev/null; then
  step "brew install python@3.12"
  brew install python@3.12 || die "brew failed to install python@3.12.
  Install manually: https://www.python.org/downloads/ and re-run."
  for cand in "/opt/homebrew/bin/python3.12" "/usr/local/bin/python3.12" \
              "$(brew --prefix python@3.12)/bin/python3.12"; do
    if py_in_range "$cand"; then PYBIN="$cand"; break; fi
  done
fi
[ -n "$PYBIN" ] || die "no python 3.10-3.12 found and brew is unavailable.
Install python 3.12 (https://www.python.org/downloads/ or
  brew install python@3.12) and re-run."
ok "python validated: $PYBIN ($("$PYBIN" -V 2>&1))"

# ── 2. stop old agent (idempotency) ────────────────────────────
step "stopping any previous GoldReaper LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true

# ── 3. copy app (preserve data/ + .env) ────────────────────────
step "copying app -> $APPDIR"
mkdir -p "$APPDIR"
if [ "$SRC" != "$APPDIR" ]; then
  rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'venv' \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'data' --exclude 'logs' --exclude '.env' \
    "$SRC"/ "$APPDIR"/
fi
mkdir -p "$APPDIR/data" "$APPDIR/logs"
# data/ is BOTH user state (excluded above) AND a code package the health
# gate runs (data/ingest_multi_tf.py). Restore the code, keep the state.
cp -f "$SRC"/data/*.py "$APPDIR/data"/ 2>/dev/null || true

# ── 4. venv at project root + pinned install ───────────────────
step "creating venv at $APPDIR/.venv (project root)"
"$PYBIN" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
if [ -f "$APPDIR/requirements.lock" ]; then
  step "installing PINNED deps from requirements.lock"
  "$VENV/bin/pip" install -r "$APPDIR/requirements.lock" -q || \
    die "pinned install failed"
else
  warn "requirements.lock missing -> requirements-paper.txt"
  "$VENV/bin/pip" install -r "$APPDIR/requirements-paper.txt" -q || \
    die "dependency install failed"
fi
ok "paper stack installed"

if [ ! -f "$APPDIR/.env" ]; then
  step "first-time setup wizard"
  (cd "$APPDIR" && "$VENV/bin/python" -m setup.wizard) || \
    warn "wizard skipped - runs at first launch"
else
  ok ".env already exists (never overwritten)"
fi

# ── 5. .app bundle (desktop icon) ───────────────────────────────
step "building Gold Reaper.app"
APPBUNDLE="$APPS_DIR/Gold Reaper.app"
mkdir -p "$APPBUNDLE/Contents/MacOS" "$APPBUNDLE/Contents/Resources"
cp -f "$APPDIR/assets/icon.icns" "$APPBUNDLE/Contents/Resources/goldreaper.icns" 2>/dev/null || true
cat > "$APPBUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Gold Reaper</string>
  <key>CFBundleIdentifier</key><string>com.goldreaper.bot</string>
  <key>CFBundleIconFile</key><string>goldreaper</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
cat > "$APPBUNDLE/Contents/MacOS/launch" <<EOF
#!/bin/bash
exec "$APPDIR/start.command"
EOF
chmod +x "$APPBUNDLE/Contents/MacOS/launch"
ok "app bundle: $APPBUNDLE"

# ── 6. LaunchAgent (KeepAlive = auto-restart) ──────────────────
step "registering LaunchAgent (KeepAlive)"
mkdir -p "$PLIST_DIR"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.goldreaper.bot</string>
  <key>WorkingDirectory</key><string>$APPDIR</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$APPDIR/bot.py</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$APPDIR/logs/bot.log</string>
  <key>StandardErrorPath</key><string>$APPDIR/logs/bot.err.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUTF8</key><string>1</string></dict>
</dict></plist>
EOF
ok "LaunchAgent registered (KeepAlive, 30s throttle)"

# ── 7. HEALTH CHECK GATE ────────────────────────────────────────
step "running the installation health check (12 steps)"
if ! (cd "$APPDIR" && "$VENV/bin/python" installer/health_check.py); then
  die "health check did not pass. Nothing was started, nothing claims
  success. Fix the step above and re-run (idempotent)."
fi

step "loading agent (gate passed)"
launchctl load "$PLIST" 2>/dev/null || true

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} GOLD REAPER INSTALLED — health check passed${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  app        : $APPDIR"
echo "  venv       : $VENV"
echo "  dashboard  : http://localhost:8080"
echo "  doctor     : $VENV/bin/python $APPDIR/cli.py doctor"
echo "  uninstall  : $SRC/install_macos.sh --uninstall [--purge]"
echo ""
echo -e "${RED}  RULE #1: PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
