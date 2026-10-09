#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: Linux installer + boot autostart
#  usage:  chmod +x install_linux.sh && ./install_linux.sh
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; NC='\033[0m'
echo -e "${RED}██_gold-reaper linux installer${NC}"

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 1. python + venv
if ! command -v python3 >/dev/null; then
  echo -e "${YEL}[i] installing python3...${NC}"
  (sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip) || \
  (sudo dnf install -y python3 python3-pip)
fi
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 2. config
if [ ! -f .env ]; then
  cp .env.example .env
  echo -e "${YEL}[i] created .env from template — EDIT IT before going live${NC}"
fi

# 3. fetch history + first backtest
echo -e "${YEL}[i] fetching historical data...${NC}"
python3 data/fetch_history.py || true

# 4. systemd user service (autostart on boot, auto-restart on crash)
SERVICE_NAME="gold-reaper.service"
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_DIR/$SERVICE_NAME" <<EOF
[Unit]
Description=GOLD REAPER :: XAUUSD autonomous hunter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/bot.py
Restart=always
RestartSec=30
StandardOutput=append:$DIR/data/reaper.log
StandardError=append:$DIR/data/reaper.err.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
echo -e "${GREEN}[✓] systemd service installed + enabled on boot${NC}"

# 5. loginctl enable-linger so it runs without being logged in
loginctl enable-linger "$USER" 2>/dev/null || true

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} REAPER INSTALLED${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  start now     : systemctl --user start gold-reaper"
echo "  stop          : systemctl --user stop gold-reaper"
echo "  status        : systemctl --user status gold-reaper"
echo "  live logs     : tail -f data/reaper.log"
echo "  config        : edit .env then restart the service"
echo ""
echo -e "${RED}  RULE #1: run PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${RED}  RULE #2: never risk money you cannot burn.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
