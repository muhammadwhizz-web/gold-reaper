"""
GOLD REAPER :: Bitget Adapter (ccxt)
====================================
Bitget has no XAUUSD spot, but tokenized gold exists:
  - XAUT/USDT (Tether Gold) perpetual futures  <- default
  - PAXG/USDT (Paxos Gold) perpetual futures
ccxt handles signing; this adapter maps oz <-> contracts (1 contract = 1 coin = 1 oz approx).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from core.logger import cprint, CYAN, RED, YELLOW

try:
    import ccxt
    CCXT_OK = True
except ImportError:
    ccxt = None  # type: ignore
    CCXT_OK = False


class BitgetBroker(BrokerBase):
    name = "BITGET"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.x = None
        self.symbol = cfg.bitget_symbol
        self.connected = False

    # ------------------------------------------------------------- connect
    def connect(self) -> bool:
        if not CCXT_OK:
            cprint("[BITGET] ccxt not installed -> pip install ccxt", RED)
            return False
        self.x = ccxt.bitget({
            "apiKey": self.cfg.bitget_key,
            "secret": self.cfg.bitget_secret,
            "password": self.cfg.bitget_passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            if self.cfg.bitget_key:
                self.x.fetch_balance()
                mode = "LIVE"
            else:
                mode = "PUBLIC-DATA (no keys -> shadow mode)"
            markets = self.x.load_markets()
            if self.symbol not in markets:
                alt = [m for m in markets if "XAU" in m.upper() or "PAXG" in m.upper() or "XAUT" in m.upper()]
                cprint(f"[BITGET] {self.symbol} not found. gold-like markets: {alt[:8]}", YELLOW)
                if alt:
                    self.symbol = alt[0]
                else:
                    return False
            m = markets[self.symbol]
            cprint(f"[BITGET] connected | {self.symbol} | {mode}", CYAN)
            self.connected = bool(self.cfg.bitget_key)
            if self.connected:
                try:
                    self.x.set_leverage(self.cfg.bitget_leverage, self.symbol)
                except Exception as e:  # noqa: BLE001
                    cprint(f"[BITGET] leverage set skipped: {e}", YELLOW)
            return True
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] connect failed: {e}", RED)
            return False

    def disconnect(self) -> None:
        self.connected = False

    # ------------------------------------------------------------- account
    def balance(self) -> float:
        try:
            bal = self.x.fetch_balance({"type": "swap"})
            return float(bal.get("USDT", {}).get("total") or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def equity(self) -> float:
        try:
            bal = self.x.fetch_balance({"type": "swap"})
            return float(bal.get("USDT", {}).get("total") or 0.0)
        except Exception:  # noqa: BLE001
            return self.balance()

    # ------------------------------------------------------------- market data
    def _ohlcv(self, tf: str, count: int) -> pd.DataFrame:
        ccxt_tf = "1h" if tf == "h1" else "4h" if tf == "h4" else "1d"
        rows = self.x.fetch_ohlcv(self.symbol, ccxt_tf, limit=count)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("time").drop(columns=["ts"])

    def candles(self, timeframe: str, count: int) -> dict[str, object]:
        if self.x is None:
            return {"h1": pd.DataFrame(), "h4": pd.DataFrame()}
        return {"h1": self._ohlcv("h1", count), "h4": self._ohlcv("h4", count)}

    def last_price(self) -> float:
        try:
            return float(self.x.fetch_ticker(self.symbol)["last"])
        except Exception:  # noqa: BLE001
            return 0.0

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        if not self.connected:
            return OrderResult(False, error="no api keys (shadow mode)")
        try:
            markets = self.x.load_markets()
            m = markets[self.symbol]
            amount = self.x.amount_to_precision(self.symbol, size_oz)
            oside = "buy" if side == "LONG" else "sell"
            params = {
                "stopLossPrice": self.x.price_to_precision(self.symbol, sl),
                "takeProfitPrice": self.x.price_to_precision(self.symbol, tp),
            }
            o = self.x.create_order(self.symbol, "market", oside, amount, None, params)
            cprint(f"[BITGET] {oside.upper()} {amount} {m.get('settle','')} | "
                   f"sl {sl:.2f} tp {tp:.2f}", CYAN)
            return OrderResult(True, ticket=str(o.get("id", "")),
                               price=float(o.get("average") or o.get("price") or 0))
        except Exception as e:  # noqa: BLE001
            return OrderResult(False, error=str(e)[:200])

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        try:
            self.x.edit_order(ticket, self.symbol, None, None,
                              params={"stopLossPrice": self.x.price_to_precision(self.symbol, new_sl)})
            return True
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] modify sl failed: {e}", YELLOW)
            return False

    def close_position(self, ticket: str, reason: str = "") -> float | None:
        try:
            self.x.cancel_order(ticket, self.symbol)
        except Exception:  # noqa: BLE001
            pass
        try:
            pos = self.x.fetch_positions([self.symbol])
            for p in pos:
                contracts = p.get("contracts") or 0
                if contracts:
                    side = "sell" if p["side"] == "long" else "buy"
                    o = self.x.create_order(self.symbol, "market", side, contracts, None,
                                            {"reduceOnly": True})
                    pnl = p.get("unrealizedPnl")
                    return float(pnl) if pnl is not None else None
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] close failed: {e}", RED)
        return None

    def open_positions(self) -> list[Position]:
        out: list[Position] = []
        if not self.connected:
            return out
        try:
            for p in self.x.fetch_positions([self.symbol]):
                contracts = float(p.get("contracts") or 0)
                if not contracts:
                    continue
                out.append(Position(
                    ticket=str(p.get("id") or ""),
                    side="LONG" if p.get("side") == "long" else "SHORT",
                    size_oz=contracts,
                    entry=float(p.get("entryPrice") or 0),
                    sl=float(p.get("stopLossPrice") or 0),
                    tp=float(p.get("takeProfitPrice") or 0),
                    opened_at=datetime.fromtimestamp(
                        (p.get("timestamp") or time.time() * 1000) / 1000, tz=timezone.utc),
                ))
        except Exception:  # noqa: BLE001
            pass
        return out
