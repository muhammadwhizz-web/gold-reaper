"""
GOLD REAPER :: Broker Base
==========================
Every broker speaks this dialect: get_candles, market_order,
set_sl_tp, update_sl, close_position, open_positions, balance.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    ticket: str
    side: str                 # LONG | SHORT
    size_oz: float            # gold ounces
    entry: float
    sl: float
    tp: float
    opened_at: datetime
    be_moved: bool = False
    trail_active: bool = False
    orig_sl: float = 0.0
    session: str = ""
    reason: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class OrderResult:
    ok: bool
    ticket: str = ""
    price: float = 0.0
    error: str = ""


class BrokerBase(ABC):
    name: str = "BASE"

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def balance(self) -> float: ...

    @abstractmethod
    def equity(self) -> float: ...

    @abstractmethod
    def candles(self, timeframe: str, count: int) -> dict[str, object]:
        """Returns {'h1': DataFrame, 'h4': DataFrame}"""

    @abstractmethod
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult: ...

    @abstractmethod
    def modify_sl(self, ticket: str, new_sl: float) -> bool: ...

    @abstractmethod
    def close_position(self, ticket: str, reason: str = "") -> float | None:
        """Returns realized pnl if known, else None."""

    @abstractmethod
    def open_positions(self) -> list[Position]: ...

    @abstractmethod
    def last_price(self) -> float: ...
