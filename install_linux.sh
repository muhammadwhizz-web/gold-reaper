#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: Linux one-command installer (P1-E repair build)
#═════════════════════════════════════════════════════════════════
#  Contract (branch fix/reliability-v1):
#    - detects distro + arch
#    - acquires Python 3.10-3.12 via the chain:
#        existing validated python3 -> pyenv -> uv (standalone prebuilt)
#        -> distro package manager -> FAIL with an install link
#    - venv at the project root (.venv/) - never somewhere hidden
#    - installs PINNED deps from requirements.lock (hash-verified);
#      falls back to requirements-paper.txt only if the lock is absent
#    - idempotent: stops old services first, re-running is always safe
#    - NEVER overwrites an existing .env
#    - registers systemd user services (Restart=always) + desktop icon
#    - runs installer/health_check.py and prints SUCCESS only if it passes
#      (a failed data-ingest therefore fails the install - no || true)
#    - ./install_linux.sh --uninstall   stops + removes services, venv,
#      icons. App dir, data/ and .env are preserved; add --purge to wipe.
#
#  usage:  chmod +x install_linux.sh && ./install_linux.sh
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; GRAY='\033[90m'; NC='\033[0m'
echo -e "${RED}██ gold-reaper :: linux installer (reliability build)${NC}"

SRC="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/gold-reaper"
APPDIR="$SHARE/app"
VENV="$SHARE/venv"
DESKTOP_DIR="$HOME/.local/share/applications"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICES="gold-reaper.service gold-reaper-dashboard.service gold-reaper-watchdog.service"

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
step() { echo -e "${YEL}[i]${NC} $1"; }
warn() { echo -e "${YEL}[!]${NC} $1"; }
die()  { echo -e "${RED}[!] INSTALL FAILED: $1${NC}"; exit 1; }

# ── uninstall path ─────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  step "stopping + disabling services"
  systemctl --user stop $SERVICES 2>/dev/null || true
  systemctl --user disable $SERVICES 2>/dev/null || true
  rm -f $SERVICE_DIR/gold-reaper*.service $SERVICE_DIR/gold-reaper-retrain.timer
  systemctl --user daemon-reload 2>/dev/null || true
  rm -f "$DESKTOP_DIR/gold-reaper.desktop" "$HOME/Desktop/gold-reaper.desktop" \
        "$HOME/桌面/gold-reaper.desktop" 2>/dev/null || true
  rm -rf "$VENV"
  ok "services, icons and venv removed"
  echo "  app dir   : $APPDIR (kept - remove manually or re-run with --purge)"
  echo "  your data : $APPDIR/data (kept - trade history + paper account)"
  echo "  your .env : $APPDIR/.env (kept)"
  if [ "${2:-}" = "--purge" ]; then
    rm -rf "$SHARE"
    ok "PURGED everything under $SHARE (including data/ and .env)"
  fi
  exit 0
fi

# ── 1. distro + arch detect ────────────────────────────────────
DISTRO="unknown"; PKG="unknown"
ARCH="$(uname -m)"
if [ -r /etc/os-release ]; then . /etc/os-release; DISTRO="${ID:-unknown}"; fi
case "$DISTRO" in
  ubuntu|debian|linuxmint|raspbian|pop)  PKG="apt" ;;
  fedora|rhel|centos|rocky|alma)         PKG="dnf" ;;
  arch|manjaro|endeavouros|garuda)       PKG="pacman" ;;
esac
echo -e "${GRAY}    distro: $DISTRO ($PKG) arch: $ARCH${NC}"
SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"

py_in_range() {  # $1 = python binary
  [ -x "$1" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if (3,10)<=(sys.version_info[0],sys.version_info[1])<=(3,12) else 1)' 2>/dev/null
}

# ── 2. python acquisition chain (P1-E) ─────────────────────────
PYBIN=""
step "acquiring python 3.10-3.12 (validated, never blindly trusted)"
if command -v python3 >/dev/null && py_in_range "$(command -v python3)"; then
  PYBIN="$(command -v python3)"
  ok "using validated python3: $PYBIN ($(python3 -V 2>&1))"
else
  # 2a. pyenv
  if command -v pyenv >/dev/null; then
    step "pyenv found -> ensuring 3.12"
    pyenv install -s 3.12 2>/dev/null || true
    cand="$(pyenv prefix 3.12 2>/dev/null)/bin/python3"
    py_in_range "$cand" && PYBIN="$cand" && ok "pyenv python: $PYBIN"
  fi
  # 2b. uv -> standalone prebuilt CPython
  if [ -z "$PYBIN" ]; then
    if ! command -v uv >/dev/null && command -v curl >/dev/null; then
      step "installing uv (standalone python provider)"
      curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
      export PATH="$HOME/.local/bin:$PATH"
    fi
    if command -v uv >/dev/null; then
      uv python install 3.12 >/dev/null 2>&1 || true
      cand="$(uv python find 3.12 2>/dev/null || true)"
      py_in_range "$cand" && PYBIN="$cand" && ok "standalone python via uv: $PYBIN"
    fi
  fi
  # 2c. distro package manager (last automatic resort)
  if [ -z "$PYBIN" ]; then
    step "falling back to distro packages ($PKG)"
    case "$PKG" in
      apt)
        $SUDO apt-get update -y
        $SUDO apt-get install -y python3 python3-venv python3-pip python3-dev build-essential curl ;;
      dnf)
        $SUDO dnf install -y python3 python3-pip python3-devel gcc curl ;;
      pacman)
        $SUDO pacman -Sy --noconfirm python python-pip base-devel curl ;;
      *) : ;;
    esac
    command -v python3 >/dev/null && py_in_range "$(command -v python3)" && \
      PYBIN="$(command -v python3)"
  fi
  [ -n "$PYBIN" ] || die "no python 3.10-3.12 could be acquired.
  Install it manually (https://www.python.org/downloads/), then re-run.
  see also docs/TROUBLESHOOTING.md #1."
fi

# ── 3. stop old services (idempotency) ─────────────────────────
step "stopping any previous gold-reaper services (safe re-run)"
systemctl --user stop $SERVICES 2>/dev/null || true

# ── 4. copy app (data/, .env preserved) ────────────────────────
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
# data/ is BOTH user state (excluded above so reinstalls never wipe it) AND
# a code package (data/ingest_multi_tf.py etc.) the 12-step health gate runs.
# Restore the code files; leave user state alone. (installer-e2e caught this:
# fresh installs failed health step 5 with No such file 'data/ingest_multi_tf.py')
cp -f "$SRC"/data/*.py "$APPDIR/data"/ 2>/dev/null || true
ln -sfn "app/data/reaper.log" "$SHARE/logs/reaper.log"
ok "app in place (existing data/ + .env preserved)"

# ── 5. venv at the project ROOT + pinned install ───────────────
step "creating venv at $APPDIR/.venv (project root)"
"$PYBIN" -m venv "$APPDIR/.venv"
VPIP="$APPDIR/.venv/bin/pip"
"$VPIP" install --upgrade pip -q
if [ -f "$APPDIR/requirements.lock" ]; then
  step "installing PINNED deps from requirements.lock (hash-verified)"
  "$VPIP" install -r "$APPDIR/requirements.lock" -q \
    || die "pinned install failed - see above"
else
  warn "requirements.lock missing -> requirements-paper.txt (unpinned)"
  "$VPIP" install -r "$APPDIR/requirements-paper.txt" -q \
    || die "dependency install failed"
fi
ok "paper stack installed"

# ── 6. launcher + wizard (never overwrite .env) ────────────────
cp -f "$APPDIR/start.sh" "$SHARE/start.sh" 2>/dev/null || cp -f "$SRC/start.sh" "$SHARE/start.sh"
chmod +x "$SHARE/start.sh"
if [ ! -f "$APPDIR/.env" ]; then
  step "first-time setup wizard (broker / keys / risk)"
  (cd "$APPDIR" && "$APPDIR/.venv/bin/python" -m setup.wizard) || \
    warn "wizard skipped - it will run at first launch (start.sh)"
else
  ok ".env already exists (never overwritten)"
fi

# ── 7. desktop icon ─────────────────────────────────────────────
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
  command -v gio >/dev/null && gio set "$DESKTOP_TARGET/gold-reaper.desktop" \
    metadata::trusted true 2>/dev/null || true
  ok "desktop icon: $DESKTOP_TARGET/gold-reaper.desktop"
else
  ok "applications-menu icon: $DESKTOP_DIR/gold-reaper.desktop"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" || true

# ── 8. systemd user services (Restart=always) ──────────────────
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
ExecStart=$APPDIR/.venv/bin/python $APPDIR/bot.py
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
ExecStart=$APPDIR/.venv/bin/python $APPDIR/dashboard/app.py
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
ExecStart=$APPDIR/.venv/bin/python $APPDIR/watchdog.py --interval 60
Restart=always
RestartSec=15
Environment=PYTHONUTF8=1
StandardOutput=append:$SHARE/logs/watchdog.log

[Install]
WantedBy=default.target
EOF

SYSTEMD_OK=0
if command -v systemctl >/dev/null 2>&1 && \
   systemctl --user show-environment >/dev/null 2>&1; then
  SYSTEMD_OK=1
  systemctl --user daemon-reload
  systemctl --user enable gold-reaper.service gold-reaper-dashboard.service \
                        gold-reaper-watchdog.service >/dev/null 2>&1 || true
  ok "services registered (Restart=always, 30s)"
else
  warn "systemd user bus unavailable (container/WSL?) - start.sh still runs everything"
fi

# ── 9. HEALTH CHECK GATE (PHASE 4) ─────────────────────────────
step "running the installation health check (12 steps, network included)"
if ! (cd "$APPDIR" && "$APPDIR/.venv/bin/python" installer/health_check.py); then
  echo ""
  die "health check did not pass. The bot was NOT started and nothing
  claims success. Fix the step printed above and re-run ./install_linux.sh
  (it is idempotent - re-running is always safe)."
fi

# ── 10. start services only after the gate passed ──────────────
if [ "$SYSTEMD_OK" = "1" ]; then
  step "starting services (gate passed)"
  systemctl --user start gold-reaper.service gold-reaper-dashboard.service 2>/dev/null || true
  if command -v loginctl >/dev/null && loginctl enable-linger "$USER" 2>/dev/null; then
    ok "linger enabled (runs 24/7 without login)"
  fi
fi

# ── 11. SUCCESS (only reachable when the gate passed) ──────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} GOLD REAPER INSTALLED — health check passed${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  app        : $APPDIR"
echo "  venv       : $APPDIR/.venv"
echo "  dashboard  : http://localhost:8080"
echo "  doctor     : $APPDIR/.venv/bin/python $APPDIR/cli.py doctor"
echo "  manage     : systemctl --user {status|stop|restart} gold-reaper"
echo "  uninstall  : $SRC/install_linux.sh --uninstall [--purge]"
echo ""
echo -e "${RED}  RULE #1: PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${RED}  RULE #2: never risk money you cannot burn.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
