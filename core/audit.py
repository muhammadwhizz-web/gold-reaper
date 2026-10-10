"""
GOLD REAPER APEX :: Audit Trail
===============================
Every decision, every dimension, every score - appended as JSONL.
This is the black box recorder: why did the reaper pull the trigger,
and why did it stay its hand.

Files: data/audit.jsonl (append-only, size-capped with one rotated
backup), readable by the dashboard.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT_FILE = ROOT / "data" / "audit.jsonl"
AUDIT_MAX_BYTES = 20_000_000   # ~20 MB, then rotate to .1 (one backup)

_EVENT_SIZES = {"signal": 40, "order": 40, "skip": 30, "lifecycle": 20, "risk": 30}


def _rotate_if_large() -> None:
    """Size-cap the audit trail. An always-on install previously grew the
    file unboundedly and tail()/rolling_pf() read it all into memory."""
    try:
        if AUDIT_FILE.exists() and \
                AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
            rotated = AUDIT_FILE.with_suffix(AUDIT_FILE.suffix + ".1")
            os.replace(AUDIT_FILE, rotated)
    except OSError:
        pass  # rotation is best-effort; never block the caller


def log_event(kind: str, payload: dict[str, Any]) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "data": payload,
    }
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_large()
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    except OSError:
        pass  # audit must never kill the trading loop (e.g. disk full)


def log_signal(signal_obj, regime, news, votes: dict, ml_prob: float | None,
               decision: str, why: str) -> None:
    log_event("signal", {
        "decision": decision,           # EXECUTED | VETOED_RISK | VETOED_ENSEMBLE
        "why": why,
        "side": getattr(signal_obj, "side", None),
        "entry": getattr(signal_obj, "entry", None),
        "sl": getattr(signal_obj, "sl", None),
        "tp1": getattr(signal_obj, "tp1", None),
        "confidence": getattr(signal_obj, "confidence", None),
        "regime": regime.to_dict() if hasattr(regime, "to_dict") else str(regime),
        "news": news.to_dict() if hasattr(news, "to_dict") else str(news),
        "module_votes": votes,
        "ml_prob": round(ml_prob, 4) if ml_prob is not None else None,
    })


def log_risk(message: str, state: dict) -> None:
    log_event("risk", {"message": message, "state": state})


def tail(n: int = 100) -> list[dict]:
    if not AUDIT_FILE.exists():
        return []
    try:
        # reverse-block read: only the last 256 KB, not the whole file
        with AUDIT_FILE.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 262_144))
            blob = f.read().decode("utf-8", "replace")
        lines = blob.splitlines()
        if size > 262_144 and lines:
            lines = lines[1:]   # drop the possibly-truncated first line
        lines = lines[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out
