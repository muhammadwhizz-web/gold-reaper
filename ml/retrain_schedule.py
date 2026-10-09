"""
GOLD REAPER APEX :: Weekly Auto-Retrain Scheduler
=================================================
Designed to be driven by systemd timer / Windows Task Scheduler weekly,
and by the bot itself every Sunday session-close.

Gate logic (promotion safety):
  1. retrain candidate on the latest store data
  2. candidate replaces production ONLY if OOS AUC passes the gate
     (>= 0.56 and no regression vs current production)
  3. every outcome is audited + announced

Run:  python ml/retrain_schedule.py [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ml.train_meta as tm  # noqa: E402
from core.audit import log_event  # noqa: E402


def retrain_weekly(force: bool = False) -> int:
    print("[RETRAIN] weekly cycle starting")
    log_event("lifecycle", {"event": "retrain_started", "force": force})
    code = tm.main(retrain=True)
    if code == 0:
        print("[RETRAIN] candidate PASSED gate -> production updated")
        log_event("lifecycle", {"event": "retrain_promoted"})
    elif code == 2:
        print("[RETRAIN] candidate FAILED gate -> production untouched (safe)")
        log_event("lifecycle", {"event": "retrain_rejected"})
    else:
        print("[RETRAIN] training error -> production untouched")
        log_event("lifecycle", {"event": "retrain_error", "code": code})
    try:
        from core.notify import notify
        notify("RETRAIN", "weekly retrain: "
               + ("promoted" if code == 0 else "rejected (production kept)"))
    except Exception:  # noqa: BLE001
        pass
    return code


if __name__ == "__main__":
    sys.exit(retrain_weekly("--force" in sys.argv))
