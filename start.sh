#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  GOLD REAPER :: linux launcher
#  usage:  ./start.sh [start|stop|restart|status|logs]
#  - activates the venv (share install ~/.local/share/gold-reaper
#    or a fresh clone), first run runs the setup wizard,
#    starts bot + dashboard, opens the dashboard in a browser.
# ═══════════════════════════════════════════════════════════════
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- locate layout -----------------------------------------------------
if [ -f "$ROOT/app/bot.py" ]; then
  APPDIR="$ROOT/app"; VENV="$ROOT/venv"
  [ -d "$ROOT/app/.venv" ] && VENV="$ROOT/app/.venv"
else
  APPDIR="$ROOT"; VENV="$ROOT/.venv"; [ -d "$ROOT/venv" ] && VENV="$ROOT/venv"
fi
PY="$VENV/bin/python"
# P1-E self-heal: a missing venv now REPAIRS itself by running the
# installer (idempotent) instead of exiting with a confusing error.
if [ ! -x "$PY" ]; then
  echo "[!] venv missing at $VENV - running the installer (self-heal)..."
  if [ -x "$APPDIR/install_linux.sh" ]; then
    GR_NONINTERACTIVE=1 bash "$APPDIR/install_linux.sh" || \
      { echo "[!] self-heal install failed - fix the errors above and re-run"; exit 1; }
  else
    echo "[!] install_linux.sh not found - run the installer from the repo root"; exit 1
  fi
  [ -x "$PY" ] || { echo "[!] installer ran but $PY still missing"; exit 1; }
fi

cd "$APPDIR" || exit 1
mkdir -p logs data
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

port="${DASHBOARD_PORT:-8080}"

have_systemd_unit() {
  # unit registered AND the user bus actually reachable (containers/WSL
  # can have unit files but no bus -> fall back to plain nohup mode)
  systemctl --user show-environment >/dev/null 2>&1 || return 1
  systemctl --user list-unit-files 2>/dev/null | grep -q "^gold-reaper.service"
}

open_browser() {
  if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    (xdg-open "http://localhost:$port" >/dev/null 2>&1 &) || true
  fi
}

bot_pid() {
  if [ -f data/bot.pid ]; then
    p="$(cat data/bot.pid 2>/dev/null)"
    [ -n "$p" ] && kill -0 "$p" 2>/dev/null && { echo "$p"; return; }
  fi
  pgrep -f "python.*$APPDIR/bot.py" 2>/dev/null | head -1
  return
}

cmd_start() {
  if [ ! -x "$PY" ]; then
    echo "[!] venv missing at $VENV - run install_linux.sh first"; exit 1
  fi
  if [ ! -f .env ]; then
    echo "[i] first run - starting setup wizard..."
    "$PY" -m setup.wizard || { echo "[!] wizard failed"; exit 1; }
  fi
  if [ -n "$(bot_pid)" ]; then
    echo "[i] bot already running (pid $(bot_pid))"
  elif have_systemd_unit; then
    echo "[i] starting via systemd..."
    systemctl --user start gold-reaper.service gold-reaper-dashboard.service 2>/dev/null || true
  else
    echo "[i] starting the reaper (background)..."
    nohup "$PY" bot.py >> logs/bot_console.log 2>&1 &
    echo "[i] starting dashboard on :$port ..."
    nohup "$PY" dashboard/app.py >> logs/dashboard.log 2>&1 &
  fi
  sleep 3
  open_browser
  echo ""
  echo "════════════════════════════════════════════════"
  echo " GOLD REAPER ONLINE"
  echo "════════════════════════════════════════════════"
  echo "  dashboard : http://localhost:$port"
  echo "  logs      : $APPDIR/data/reaper.log  (./start.sh logs)"
  echo "  audit     : $APPDIR/data/audit.jsonl"
  echo "  stop      : ./start.sh stop"
  echo "  status    : ./start.sh status"
  echo "  RULE #1: PAPER_MODE=true for the first 2 weeks."
  echo "════════════════════════════════════════════════"
}

cmd_stop() {
  if have_systemd_unit; then
    echo "[i] stopping systemd units..."
    systemctl --user stop gold-reaper.service gold-reaper-dashboard.service 2>/dev/null || true
  fi
  p="$(bot_pid)"
  if [ -n "$p" ]; then
    echo "[i] stopping bot pid $p ..."
    kill "$p" 2>/dev/null || true
    sleep 2
    kill -9 "$p" 2>/dev/null || true
  fi
  pkill -f "python.*$APPDIR/dashboard/app.py" 2>/dev/null || true
  rm -f data/bot.pid
  echo "[OK] reaper stopped. open trades keep their server-side SL/TP."
}

cmd_status() {
  if have_systemd_unit; then
    systemctl --user status gold-reaper.service --no-pager -l | head -12 || true
  fi
  p="$(bot_pid)"
  if [ -n "$p" ]; then
    echo "[OK] bot running (pid $p)"
  else
    echo "[--] bot not running"
  fi
  if [ -f data/heartbeat.json ]; then
    "$PY" - <<'EOF'
import json, time
try:
    hb = json.load(open("data/heartbeat.json"))
    age = time.time() - hb.get("ts", 0)
    print(f"     heartbeat: {age:.0f}s ago | mode {hb.get('mode')} | "
          f"equity {hb.get('equity')}")
except Exception as e:
    print("     heartbeat unreadable:", e)
EOF
  fi
}

cmd_logs() { tail -n 50 -f data/reaper.log; }

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; sleep 2; cmd_start ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  *) echo "usage: $0 [start|stop|restart|status|logs]"; exit 1 ;;
esac
