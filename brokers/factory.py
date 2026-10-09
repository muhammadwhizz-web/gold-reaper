"""
GOLD REAPER :: broker factory
=============================
Reads BROKER from .env / config and returns the right adapter, with a
configurable failover chain (default MT5 -> BITGET -> PAPER). The bot
never dies: if the primary broker cannot connect, the next one is tried,
and PAPER is the terminal fallback.
"""
from __future__ import annotations

import os

from brokers.base import BrokerBase

ALIASES = {
    "MT5": "MT5", "EXNESS": "MT5", "METATRADER": "MT5",
    "BITGET": "BITGET",
    "PAPER": "PAPER", "DEMO": "PAPER", "TEST": "PAPER",
}


def normalize(choice: str) -> str:
    return ALIASES.get(str(choice).strip().upper(), "PAPER")


def create_broker(cfg, choice: str | None = None) -> BrokerBase:
    """Build one adapter. Bad names degrade to PAPER, never raise."""
    kind = normalize(choice or cfg.broker)
    if kind == "MT5":
        from brokers.mt5_broker import ExnessMT5
        return ExnessMT5(cfg)
    if kind == "BITGET":
        from brokers.bitget_broker import BitgetBroker
        return BitgetBroker(cfg)
    from brokers.paper_broker import PaperBroker
    return PaperBroker(cfg)


def failover_chain(cfg) -> list[str]:
    """Primary-first chain. FAILOVER_CHAIN='BITGET,MT5' overrides order.

    Paper mode (PAPER_MODE=true or BROKER=PAPER) always collapses to a
    single-element chain: paper is where you TEST, not where you hide.
    """
    if getattr(cfg, "paper", False) or normalize(cfg.broker) == "PAPER":
        return ["PAPER"]
    raw = os.getenv("FAILOVER_CHAIN", "").strip()
    if raw:
        chain: list[str] = []
        for part in raw.split(","):
            k = normalize(part)
            if k not in chain:
                chain.append(k)
        return chain
    chain = [normalize(cfg.broker)]
    for k in ("MT5", "BITGET", "PAPER"):
        if k not in chain:
            chain.append(k)
    return chain


def connect_with_failover(cfg, log) -> tuple[BrokerBase, list[str]]:
    """Try every link in the chain. Returns (broker, attempted).

    Guaranteed to return a connected PaperBroker even if everything fails.
    """
    attempted: list[str] = []
    for kind in failover_chain(cfg):
        attempted.append(kind)
        broker = create_broker(cfg, kind)
        try:
            if broker.connect():
                return broker, attempted
        except Exception as e:  # noqa: BLE001
            if log:
                log.warning("broker %s connect error: %s", kind, e)
        try:
            broker.disconnect()
        except Exception:  # noqa: BLE001
            pass
    fallback = create_broker(cfg, "PAPER")
    fallback.connect()
    return fallback, attempted
