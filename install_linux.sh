#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER APEX :: Linux installer + boot autostart
#  bot (systemd) + dashboard (:8050) + weekly retrain timer
#  usage:  chmod +x install_linux.sh && ./install_linux.sh
# ═══════════════════════════════════════════════════════════════
set -e
RED='\033[31m'; GREEN='\033[32m'; YEL='\033[33m'; NC='\033[0m'
echo -e "${RED}██ gold-reaper APEX linux installer${NC}"

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

# 3. data layer: multi-tf ingestion + features + calendar
echo -e "${YEL}[i] ingesting multi-timeframe history...${NC}"
python3 data/ingest_multi_tf.py || true
echo -e "${YEL}[i] forging feature matrix...${NC}"
python3 features/build_features.py || true
echo -e "${YEL}[i] pulling economic calendar...${NC}"
python3 data/news_ingest.py || true

# 4. systemd user services
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/gold-reaper.service" <<EOF
[Unit]
Description=GOLD REAPER APEX :: XAUUSD autonomous hunter
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

cat > "$SERVICE_DIR/gold-reaper-dashboard.service" <<EOF
[Unit]
Description=GOLD REAPER APEX :: live dashboard :8050
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/core/dashboard.py 8050
Restart=always
RestartSec=15
StandardOutput=append:$DIR/data/dashboard.log

[Install]
WantedBy=default.target
EOF

cat > "$SERVICE_DIR/gold-reaper-retrain.service" <<EOF
[Unit]
Description=GOLD REAPER APEX :: weekly meta-model retrain

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/ml/retrain_schedule.py
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

systemctl --user daemon-reload
systemctl --user enable gold-reaper.service gold-reaper-dashboard.service
systemctl --user enable --now gold-reaper-retrain.timer
loginctl enable-linger "$USER" 2>/dev/null || true

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN} APEX INSTALLED${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo "  bot           : systemctl --user start gold-reaper"
echo "  dashboard     : http://localhost:8050  (auto-starts on boot)"
echo "  weekly retrain: systemctl --user list-timers | grep retrain"
echo "  logs          : tail -f data/reaper.log"
echo "  audit trail   : tail -f data/audit.jsonl"
echo "  config        : edit .env then restart the service"
echo "  breakers      : python bot.py --reset-breakers"
echo ""
echo -e "${RED}  RULE #1: run PAPER_MODE=true for at least 2 weeks.${NC}"
echo -e "${RED}  RULE #2: never risk money you cannot burn.${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
