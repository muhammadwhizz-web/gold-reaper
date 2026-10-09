#!/usr/bin/env python3
"""
GOLD REAPER :: system tray (optional)
=====================================
Small tray icon with a status LED:
  green  = bot heartbeat fresh
  red    = heartbeat stale or breaker latched
  gray   = no bot running / dashboard off

Menu:
  Open Dashboard   -> http://localhost:8080
  Stop Reaper      -> terminates the bot (pid from heartbeat.json)
  Exit Tray

Needs pystray + Pillow (installed by the installers). On Linux it uses
the AppIndicator backend when available; if the desktop has no tray
host, it prints a hint and exits quietly — the bot does not depend on it.

Run:  python system_tray.py
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if os.name == "nt":
    try:
        _out: Any = sys.stdout
        _out.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

HEARTBEAT = ROOT / "data" / "heartbeat.json"
DASHBOARD_URL = f"http://localhost:{os.getenv('DASHBOARD_PORT', '8080')}"
STALE_S = float(os.getenv("WATCHDOG_STALE_SECONDS", "180"))

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_OK = True
except ImportError:
    TRAY_OK = False
    pystray = None  # type: ignore[assignment]

GREEN = (0, 255, 156)
RED = (255, 68, 68)
GRAY = (110, 120, 116)


def _led_icon(color: tuple) -> "Image.Image":
    """Tiny dark tray square with the LED in the corner."""
    img = Image.new("RGBA", (64, 64), (10, 14, 15, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, 61, 61], outline=(0, 255, 156, 255), width=3)
    d.rectangle([6, 6, 57, 57], outline=(0, 255, 156, 90), width=1)
    d.ellipse([38, 38, 58, 58], fill=color + (255,))
    return img


def _bot_state() -> tuple[str, float | None]:
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        age = time.time() - float(hb.get("ts", 0))
        if age > STALE_S:
            return "stale", age
        return "alive", age
    except Exception:  # noqa: BLE001
        return "dead", None


def _stop_bot() -> None:
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        pid = int(hb.get("pid", 0))
        if pid > 0:
            if os.name == "nt":
                os.system(f"taskkill /PID {pid} /T /F >nul 2>&1")  # noqa: S605
            else:
                os.kill(pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    if not TRAY_OK:
        print("[tray] pystray/Pillow not installed -> "
              "pip install pystray pillow (bot runs fine without the tray)")
        return 0
    try:
        import pystray

        icon_stop = threading.Event()

        def _stop(*_: object) -> None:
            _stop_bot()

        icon = pystray.Icon(
            "gold-reaper",
            icon=_led_icon(GREEN),
            title="Gold Reaper",
            menu=pystray.Menu(
                pystray.MenuItem("Open Dashboard",
                                 lambda *_: webbrowser.open(DASHBOARD_URL),
                                 default=True),
                pystray.MenuItem("Stop Reaper", _stop),
                pystray.MenuItem("Exit Tray",
                                 lambda *_: icon_stop.set()),
            ),
        )

        def refresher() -> None:
            while not icon_stop.wait(10.0):
                state, _ = _bot_state()
                icon.icon = _led_icon({"alive": GREEN, "stale": RED,
                                       "dead": GRAY}[state])
                icon.title = f"Gold Reaper — bot {state}"

        threading.Thread(target=refresher, daemon=True).start()
        print(f"[tray] online | dashboard {DASHBOARD_URL} | close = exit")
        icon.run()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[tray] unavailable on this desktop ({e}); "
              f"the bot does not need it — use start.sh / Task Scheduler")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
