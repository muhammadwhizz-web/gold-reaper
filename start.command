#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: macOS launcher (double-clickable .command)
#  Wrapper over start.sh with native browser open. Starts the bot
#  and dashboard, opens http://localhost:8080 in Safari/default.
# ═══════════════════════════════════════════════════════════════
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$ROOT/app/bot.py" ]; then
  APPDIR="$ROOT/app"; VENV="$ROOT/venv"
else
  APPDIR="$ROOT"; VENV="$ROOT/.venv"; [ -d "$ROOT/venv" ] && VENV="$ROOT/venv"
fi
cd "$APPDIR" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
port="${DASHBOARD_PORT:-8080}"

PY="$VENV/bin/python"
# P1-E self-heal: missing venv -> run the installer instead of dying
if [ ! -x "$PY" ]; then
  echo "[!] venv missing at $VENV - running the installer (self-heal)..."
  if [ -x "$APPDIR/install_macos.sh" ]; then
    GR_NONINTERACTIVE=1 bash "$APPDIR/install_macos.sh" || \
      { echo "[!] self-heal install failed - fix the errors above and re-run"; exit 1; }
  else
    echo "[!] install_macos.sh not found - run the installer from the repo root"; exit 1
  fi
  [ -x "$PY" ] || { echo "[!] installer ran but $PY still missing"; exit 1; }
fi

if [ ! -f .env ]; then
  echo "[i] first run - starting setup wizard..."
  "$PY" -m setup.wizard || exit 1
fi

echo "[i] starting the reaper..."
nohup "$PY" bot.py >> logs/bot_console.log 2>&1 &
echo "[i] starting dashboard on :$port ..."
nohup "$PY" dashboard/app.py >> logs/dashboard.log 2>&1 &
sleep 3
open "http://localhost:$port" || true

echo ""
echo "════════════════════════════════════════════════"
echo " GOLD REAPER ONLINE"
echo "════════════════════════════════════════════════"
echo "  dashboard : http://localhost:$port"
echo "  logs      : $APPDIR/data/reaper.log"
echo "  stop      : pkill -f '$APPDIR/bot.py'"
echo "  (LaunchAgent com.goldreaper.bot keeps it alive 24/7)"
echo "════════════════════════════════════════════════"
# keep the Terminal window open briefly so double-click users see status
sleep 5
exit 0
