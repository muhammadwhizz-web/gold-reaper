"""
GOLD REAPER :: broker factory
=============================
Builds the requested broker adapter and connects it EXPLICITLY.

REPAIR CONTRACT (branch fix/reliability-v1, P1-B):
  - ``requested_broker`` (from .env / CLI, normalized) and
    ``active_broker`` (actually connected) are tracked as two values.
  - A live broker that fails to connect HALTS the bot with a precise
    error (BrokerHaltError -> exit code 3). The bot NEVER silently swaps
    live <-> paper: if you asked for MT5 you will never wake up on a
    paper account that looks live.
  - Paper fallback for a live request is forbidden even with
    ALLOW_PAPER_FALLBACK=true (that flag only ever governs runs where
    the user explicitly requested paper).
  - Live order failures must never be reported as successful: adapters
    return ok=False and the bot journals them as rejections.
"""
from __future__ import annotations

import os

from brokers.base import BrokerBase

ALIASES = {
    "MT5": "MT5", "EXNESS": "MT5", "METATRADER": "MT5",
    "BITGET": "BITGET",
    "PAPER": "PAPER", "DEMO": "PAPER", "TEST": "PAPER",
}

LIVE_BROKERS = ("MT5", "BITGET")


class BrokerHaltError(RuntimeError):
    """The requested broker could not be brought up safely.

    The bot must halt (exit code 3) and require a manual .env change -
    never fall back to paper silently."""


def normalize(choice: str) -> str:
    """Normalize a broker name. Unknown names are NOT coerced to PAPER
    silently anymore - normalize_valid() is the strict path."""
    return ALIASES.get(str(choice).strip().upper(), "UNKNOWN")


def normalize_valid(choice: str) -> str:
    """Strict normalization. Raises BrokerHaltError on unknown names
    (P1-C sibling: no silent PAPER degradation for garbage config)."""
    kind = normalize(choice)
    if kind == "UNKNOWN":
        raise BrokerHaltError(
            f"unknown broker {choice!r} - valid: MT5, BITGET, PAPER "
            f"(fix BROKER in .env)")
    return kind


def create_broker(cfg, choice: str | None = None) -> BrokerBase:
    """Build one adapter for a VALID kind (raises on unknown names)."""
    kind = normalize_valid(choice or cfg.broker)
    if kind == "MT5":
        from brokers.mt5_broker import ExnessMT5
        return ExnessMT5(cfg)
    if kind == "BITGET":
        from brokers.bitget_broker import BitgetBroker
        return BitgetBroker(cfg)
    from brokers.paper_broker import PaperBroker
    return PaperBroker(cfg)


def failover_chain(cfg) -> list[str]:
    """P1-B: the chain is exactly the requested broker.

    The old MT5 -> BITGET -> PAPER auto-failover is gone: a live broker
    failing must halt the bot, not quietly re-route to another venue or
    to paper. Kept as a function because tests and tooling import it.
    """
    return [normalize_valid(cfg.broker)]


def paper_fallback_allowed(cfg) -> bool:
    """Paper may only ever trade when the user REQUESTED paper. The
    ALLOW_PAPER_FALLBACK flag exists for explicit opt-in tooling and
    still cannot authorize a live->paper swap."""
    return os.getenv("ALLOW_PAPER_FALLBACK", "").strip().lower() in \
        ("1", "true", "yes", "on") and normalize(cfg.broker) == "PAPER"


def connect_with_failover(cfg, log) -> tuple[BrokerBase, list[str]]:
    """Connect the requested broker or raise BrokerHaltError.

    Returns (broker, attempted). Never returns a different broker kind
    than the one requested: live -> paper swaps are forbidden (P1-B)."""
    requested = normalize_valid(cfg.broker)
    attempted: list[str] = [requested]
    broker = create_broker(cfg, requested)
    try:
        if broker.connect():
            return broker, attempted
        reason = getattr(broker, "status_reason", "") or \
            "connect() returned False"
    except Exception as e:  # noqa: BLE001 - classified into a halt
        reason = f"{type(e).__name__}: {e}"
    try:
        broker.disconnect()
    except Exception:  # noqa: BLE001
        pass
    raise BrokerHaltError(
        f"requested broker {requested} failed to connect: {reason}. "
        f"Fix the broker config in .env (or set BROKER=PAPER for honest "
        f"paper trading) and start again. No silent fallback was taken.")
