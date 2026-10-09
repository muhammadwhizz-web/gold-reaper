#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: Linux one-command installer
#═════════════════════════════════════════════════════════════════
#  What it does (fresh Ubuntu/Debian/Fedora/Arch -> hunting):
#    1. detects distro via /etc/os-release -> apt | dnf | pacman
#    2. checks Python 3.10+ (installs via the package manager)
#    3. copies the app  -> ~/.local/share/gold-reaper/app
#    4. creates venv    -> ~/.local/share/gold-reaper/venv
#    5. installs requirements (MetaTrader5 is Windows-only and is
#       auto-skipped by pip markers - Linux hunts via Bitget/Paper)
#    6. ingests history + features + calendar
#    7. runs the first-time setup wizard (broker, keys, risk)
#    8. creates a desktop icon:
#         ~/.local/share/applications/gold-reaper.desktop
#         ~/Desktop/gold-reaper.desktop (double-click to launch)
#    9. registers systemd user services (Restart=always, 30s):
#         gold-reaper.service            bot
#         gold-reaper-dashboard.service  dashboard :8050/8080
#         gold-reaper-watchdog.service   watchdog
#         gold-reaper-retrain.timer      weekly retrain
#   10. enables linger -> runs 24/7 even with no login session
#
#  usage:  chmod +x install_linux.sh && ./install_linux.sh
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; GRAY='\033[90m'; NC='\033[0m'
echo -e "${RED}██ gold-reaper :: linux installer${NC}"

SRC="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/gold-reaper"
APPDIR="$SHARE/app"
VENV="$SHARE/venv"
DESKTOP_DIR="$HOME/.local/share/applications"
SERVICE_DIR="$HOME/.config/systemd/user"

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
step() { echo -e "${YEL}[i]${NC} $1"; }
die()  { echo -e "${RED}[!]${NC} $1"; exit 1; }

# ---- 1. distro detect + python ------------------------------------------
DISTRO="unknown"; PKG="unknown"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  DISTRO="${ID:-unknown}"
fi
case "$DISTRO" in
  ubuntu|debian|linuxmint|raspbian|pop)  PKG="apt" ;;
  fedora|rhel|centos|rocky|alma)         PKG="dnf" ;;
  arch|manjaro|endeavouros|garuda)       PKG="pacman" ;;
  *) PKG="unknown" ;;
esac
echo -e "${GRAY}    distro: $DISTRO ($PKG)${NC}"

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"

py_ok() {
  command -v python3 >/dev/null || return 1
  v="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null)" || return 1
  python3 - "$v" <<'EOF'
import sys
v = sys.argv[1]
maj, mi = (int(x) for x in v.split("."))
sys.exit(0 if (maj, mi) >= (3, 10) and (maj, mi) < (3, 13) else 1)
EOF
}

if ! py_ok; then
  step "python 3.10-3.12 missing -> installing via $PKG..."
  case "$PKG" in
    apt)
      $SUDO apt-get update -y
      $SUDO apt-get install -y python3 python3-venv python3-pip python3-dev build-essential curl ;;
    dnf)
      $SUDO dnf install -y python3 python3-pip python3-devel gcc curl ;;
    pacman)
      $SUDO pacman -Sy --noconfirm python python-pip base-devel curl ;;
    *)
      die "unsupported distro '$DISTRO'. Install Python 3.10-3.12 manually, then re-run."
      ;;
  esac
fi
py_ok || die "python >= 3.10 still missing after install attempt. See docs/TROUBLESHOOTING.md #1."
ok "python $(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"

# ---- 2. copy app ----------------------------------------------------------
step "copying app -> $APPDIR"
mkdir -p "$SHARE" "$APPDIR" "$SHARE/logs"
if [ "$SRC" != "$APPDIR" ]; then
  rsync -a --delete \
    --exclude '.git' --exclude '.gitignore' \
    --exclude '.venv' --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'data' --exclude 'logs' --exclude '.env' \
    --exclude 'installer/output' \
    "$SRC"/ "$APPDIR"/ 2>/dev/null || \
  { mkdir -p "$APPDIR" && cp -r "$SRC"/. "$APPDIR"/ \
      && rm -rf "$APPDIR/.git" "$APPDIR/.venv" "$APPDIR/venv" "$APPDIR/data" "$APPDIR/.env" "$APPDIR/logs"; }
fi
mkdir -p "$APPDIR/data"
# logs/reaper.log symlink -> the bot's real log (both paths work)
ln -sfn "app/data/reaper.log" "$SHARE/logs/reaper.log"
ok "app in place (your data/, .env preserved if present)"

# ---- 3. venv + requirements ------------------------------------------------
step "creating venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
step "installing requirements (a few minutes)..."
"$VENV/bin/pip" install -r "$APPDIR/requirements.txt" -q
ok "APEX stack installed (MetaTrader5 auto-skipped on Linux)"

# ---- 4. launcher + wizard ---------------------------------------------------
cp -f "$SRC/start.sh" "$SHARE/start.sh" 2>/dev/null || cp -f "$APPDIR/start.sh" "$SHARE/start.sh"
chmod +x "$SHARE/start.sh"

if [ ! -f "$APPDIR/.env" ]; then
  step "first-time setup wizard (broker / keys / risk)"
  (cd "$APPDIR" && "$VENV/bin/python" -m setup.wizard) || \
    step "wizard skipped - it will run at first launch (start.sh)"
else
  ok ".env already exists (wizard preserved your config)"
fi

# ---- 5. data layer -----------------------------------------------------------
step "ingesting multi-timeframe history (background-tolerant)..."
(cd "$APPDIR" && "$VENV/bin/python" data/ingest_multi_tf.py) || true
step "forging feature matrix..."
(cd "$APPDIR" && "$VENV/bin/python" features/build_features.py) || true
step "pulling economic calendar..."
(cd "$APPDIR" && "$VENV/bin/python" data/news_ingest.py) || true

# ---- 6. desktop icon ----------------------------------------------------------
step "creating desktop icon"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/gold-reaper.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Gold Reaper
Comment=Autonomous XAU/USD hunter
Exec=$SHARE/start.sh
Icon=$APPDIR/assets/icon.png
Terminal=true
Categories=Office;Finance;
EOF
chmod +x "$DESKTOP_DIR/gold-reaper.desktop"
DESKTOP_TARGET=""
for d in "$HOME/Desktop" "$HOME/桌面"; do
  [ -d "$d" ] && DESKTOP_TARGET="$d" && break
done
if [ -n "$DESKTOP_TARGET" ]; then
  cp -f "$DESKTOP_DIR/gold-reaper.desktop" "$DESKTOP_TARGET/gold-reaper.desktop"
  chmod +x "$DESKTOP_TARGET/gold-reaper.desktop"
  # GNOME needs this metadata bit to trust launcher files
  command -v gio >/dev/null && gio set "$DESKTOP_TARGET/gold-reaper.desktop" \
    metadata::trusted true 2>/dev/null || true
  ok "desktop icon: $DESKTOP_TARGET/gold-reaper.desktop"
else
  ok "applications-menu icon: $DESKTOP_DIR/gold-reaper.desktop (no Desktop dir found)"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" || true

# ---- 7. systemd user services ---------------------------------------------------
step "registering systemd user services"
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_DIR/gold-reaper.service" <<EOF
[Unit]
Description=GOLD REAPER :: XAUUSD autonomous hunter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APPDIR
ExecStart=$VENV/bin/python $APPDIR/bot.py
Restart=always
RestartSec=30
Environment=PYTHONUTF8=1
StandardOutput=append:$SHARE/logs/bot.log
StandardError=append:$SHARE/logs/bot.err.log

[Install]
WantedBy=default.target
EOF

cat > "$SERVICE_DIR/gold-reaper-dashboard.service" <<EOF
[Unit]
Description=GOLD REAPER :: live dashboard :8080
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$APPDIR
ExecStart=$VENV/bin/python $APPDIR/dashboard/app.py
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
StandardOutput=append:$SHARE/logs/dashboard.log
StandardError=append:$SHARE/logs/dashboard.err.log

[Install]
WantedBy=default.target
EOF

cat > "$SERVICE_DIR/gold-reaper-watchdog.service" <<EOF
[Unit]
Description=GOLD REAPER :: watchdog (restarts the bot if it wedges)
After=gold-reaper.service

[Service]
Type=simple
WorkingDirectory=$APPDIR
ExecStart=$VENV/bin/python $APPDIR/watchdog.py --interval 60
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
StandardOutput=append:$SHARE/logs/watchdog.log

[Install]
WantedBy=default.target
EOF

cat > "$SERVICE_DIR/gold-reaper-retrain.service" <<EOF
[Unit]
Description=GOLD REAPER :: weekly meta-model retrain

[Service]
Type=oneshot
WorkingDirectory=$APPDIR
ExecStart=$VENV/bin/python $APPDIR/ml/retrain_schedule.py
EOF

cat > "$SERVICE_DIR/gold-reaper-retrain.timer" <<EOF
[Unit]
Description=Weekly retrain (Sunday 21:30 UTC after market close)

[Timer]
OnCalendar=Sun *-*-* 21:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

SYSTEMD_OK=0
if command -v systemctl >/dev/null 2>&1 && \
   systemctl --user show-environment >/dev/null 2>&1; then
  SYSTEMD_OK=1
  systemctl --user daemon-reload
  systemctl --user enable gold-reaper.service gold-reaper-dashboard.service \
                        gold-reaper-watchdog.service >/dev/null 2>&1 || true
  systemctl --user enable --now gold-reaper-retrain.timer >/dev/null 2>&1 || true

  step "starting services now..."
  systemctl --user start gold-reaper.service gold-reaper-dashboard.service 2>/dev/null || true
  ok "systemd services registered (Restart=always, 30s)"
else
  echo -e "${YEL}[i]${NC} systemd user bus not available (container/WSL/minimal server?)"
  echo -e "${YEL}    ${NC}bot is still fully usable: run ./start.sh (launchers) -"
  echo -e "${YEL}    ${NC}the in-bot supervisor + watchdog.py provide the same 24/7 safety."
fi

# ---- 8. linger: run 24/7 with no login session ------------------------------
if [ "$SYSTEMD_OK" = "1" ] && command -v loginctl >/dev/null; then
  if loginctl enable-linger "$USER" 2>/dev/null; then
    ok "linger enabled (runs without login)"
  else
    echo -e "${YEL}[i]${NC} enable linger manually once: loginctl enable-linger $USER"
  fi
fi

# ---- 9. success ----------------------------------------------------------------
echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} GOLD REAPER INSTALLED${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  app        : $APPDIR"
echo "  venv       : $VENV"
echo "  dashboard  : http://localhost:8080"
echo "  bot log    : $APPDIR/data/reaper.log  (or $SHARE/logs/reaper.log)"
echo "  audit trail: $APPDIR/data/audit.jsonl"
if [ "$SYSTEMD_OK" = "1" ]; then
  echo "  manage     : systemctl --user {status|stop|restart} gold-reaper"
  echo "  logs       : journalctl --user -u gold-reaper -f"
else
  echo "  launch     : $SHARE/start.sh   (bot + dashboard + browser)"
  echo "  status     : $SHARE/start.sh status"
fi
echo "  stop       : $SHARE/start.sh stop"
echo "  breakers   : $VENV/bin/python $APPDIR/bot.py --reset-breakers"
echo "  uninstall  : see docs/TROUBLESHOOTING.md #13"
echo ""
echo -e "${RED}  RULE #1: PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${RED}  RULE #2: never risk money you cannot burn.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
