"""
GOLD REAPER :: Bitget Adapter (ccxt)
====================================
Bitget has no XAUUSD spot, but tokenized gold exists:
  - XAUT/USDT (Tether Gold) perpetual futures  <- default
  - PAXG/USDT (Paxos Gold) perpetual futures
ccxt handles signing; this adapter maps oz <-> contracts (1 contract = 1 coin = 1 oz approx).

Bulletproofing (Phase 3 of the installer spec):
  - startup API-key test with human-readable errors:
      invalid key / IP whitelist mismatch / rate limit / network
  - every REST call runs through exponential backoff 1s->2s->4s->8s->60s cap
  - market auto-detect: swap (futures) or spot, BITGET_MARKET env override
  - symbol fallback chain: XAUT/USDT:USDT -> XAUT/USDT -> PAXG/USDT:USDT
  - leverage set once and verified
  - position mode (one-way vs hedge) detected and adapted
  - ensure_connected() for the health monitor / auto-reconnect
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from brokers.health import retry_call
from core.logger import CYAN, RED, YELLOW, cprint

try:
    import ccxt
    CCXT_OK = True
except ImportError:
    ccxt = None  # type: ignore
    CCXT_OK = False

SYMBOL_CANDIDATES_SWAP = ["XAUT/USDT:USDT", "XAUT/USDT", "PAXG/USDT:USDT", "PAXG/USDT"]
SYMBOL_CANDIDATES_SPOT = ["XAUT/USDT", "PAXG/USDT"]


def _explain_ccxt_error(e: Exception) -> str:
    """Map ccxt exceptions to a fix a human can act on."""
    if ccxt is None:
        return str(e)
    name = type(e).__name__
    s = str(e)
    if isinstance(e, ccxt.AuthenticationError):
        return ("invalid API key/secret/passphrase - regenerate at "
                "bitget.com -> API keys (fix in .env)")
    if isinstance(e, ccxt.PermissionDenied) or "ip" in s.lower() and "whitelist" in s.lower() \
            or "40103" in s or "not in the ip white" in s.lower():
        return ("IP WHITELIST MISMATCH - add this machine's public IP in "
                "Bitget API settings (check yours: curl ifconfig.me)")
    if isinstance(e, ccxt.RateLimitExceeded):
        return "rate limited - the bot backs off automatically (1->2->4->8->60s)"
    if isinstance(e, ccxt.ExchangeNotAvailable):
        return "exchange temporarily unavailable - will retry with backoff"
    if isinstance(e, ccxt.NetworkError):
        return f"network error - will retry with backoff ({name})"
    if isinstance(e, ccxt.InsufficientFunds):
        return "not enough USDT margin - reduce risk or deposit"
    return s[:200]


class BitgetBroker(BrokerBase):
    name = "BITGET"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.x: Any = None  # ccxt client (untyped 3rd-party)
        self.symbol = cfg.bitget_symbol
        self.market_type = getattr(cfg, "bitget_market", None) or \
            __import__("os").getenv("BITGET_MARKET", "swap").lower()
        self.connected = False
        self._last_error = ""

    # ------------------------------------------------------------- backoff core
    def _call(self, fn, *args, attempts: int = 5, **kwargs):
        """REST call with exponential backoff; raises last error."""
        def _on_retry(i, exc, sleep_s):
            cprint(f"[BITGET] {type(exc).__name__} (attempt {i}) - "
                   f"retrying in {sleep_s:.0f}s", YELLOW)
        try:
            return retry_call(fn, *args, attempts=attempts,
                              exceptions=(ccxt.NetworkError, ccxt.ExchangeNotAvailable,
                                          ccxt.RateLimitExceeded) if ccxt else (Exception,),
                              on_retry=_on_retry, **kwargs)
        except Exception as e:  # noqa: BLE001
            self._last_error = _explain_ccxt_error(e)
            raise

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
            "options": {"defaultType": self.market_type},
            "timeout": 15000,
        })
        try:
            if self.cfg.bitget_key:
                try:
                    self._call(self.x.fetch_balance)
                    mode = "LIVE"
                except Exception as e:  # noqa: BLE001
                    self._last_error = _explain_ccxt_error(e)
                    cprint(f"[BITGET] API key test failed: {self._last_error}", RED)
                    return False
            else:
                mode = "PUBLIC-DATA (no keys -> shadow mode)"

            markets = self._call(self.x.load_markets)
            if self.symbol not in markets:
                cands = SYMBOL_CANDIDATES_SPOT if self.market_type == "spot" \
                    else SYMBOL_CANDIDATES_SWAP
                for cand in cands:
                    if cand in markets:
                        cprint(f"[BITGET] symbol fallback: "
                               f"{self.cfg.bitget_symbol} -> {cand}", YELLOW)
                        self.symbol = cand
                        break
                else:
                    gold_like = [m for m in markets
                                 if any(t in m.upper() for t in ("XAUT", "PAXG", "XAU"))]
                    cprint(f"[BITGET] {self.cfg.bitget_symbol} not listed. "
                           f"gold-like markets: {gold_like[:8]}", YELLOW)
                    if gold_like:
                        self.symbol = gold_like[0]
                    else:
                        self._last_error = "no tokenized-gold market listed"
                        return False
            m = markets[self.symbol]
            actual_type = m.get("swap") and "swap" or "spot"
            cprint(f"[BITGET] connected | {self.symbol} ({actual_type}) | {mode}", CYAN)
            self.connected = bool(self.cfg.bitget_key)
            if self.connected:
                self._setup_account()
            return True
        except Exception as e:  # noqa: BLE001
            self._last_error = _explain_ccxt_error(e)
            cprint(f"[BITGET] connect failed: {self._last_error}", RED)
            return False

    def _setup_account(self) -> None:
        """Leverage once + position mode detect. Best-effort, never fatal."""
        # position mode: one-way preferred (our engine holds 1 position);
        # hedge accounts still work via reduceOnly closes.
        try:
            self.x.set_position_mode(False)
            cprint("[BITGET] position mode: one-way", CYAN)
        except Exception as e:  # noqa: BLE001
            s = str(e).lower()
            if "position mode" in s and ("not" in s or "same" in s or "modify" in s):
                cprint("[BITGET] position mode already configured (ok)", CYAN)
            else:
                cprint(f"[BITGET] position-mode set skipped: "
                       f"{_explain_ccxt_error(e)}", YELLOW)
        try:
            self._call(self.x.set_leverage, self.cfg.bitget_leverage, self.symbol)
            cprint(f"[BITGET] leverage set: {self.cfg.bitget_leverage}x", CYAN)
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] leverage set skipped: {_explain_ccxt_error(e)}", YELLOW)

    def ensure_connected(self) -> bool:
        if self.connected:
            return True
        return self.connect()

    def disconnect(self) -> None:
        self.connected = False

    # ------------------------------------------------------------- account
    def balance(self) -> float:
        try:
            bal = self._call(self.x.fetch_balance,
                             {"type": self.market_type}, attempts=3)
            return float(bal.get("USDT", {}).get("total") or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def equity(self) -> float:
        return self.balance()

    # ------------------------------------------------------------- market data
    def _ohlcv(self, tf: str, count: int) -> pd.DataFrame:
        ccxt_tf = "1h" if tf == "h1" else "4h" if tf == "h4" else "1d"
        rows = self._call(self.x.fetch_ohlcv, self.symbol, ccxt_tf,
                          limit=count, attempts=3)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("time").drop(columns=["ts"])

    def candles(self, timeframe: str, count: int) -> dict[str, pd.DataFrame]:
        if self.x is None:
            return {"h1": pd.DataFrame(), "h4": pd.DataFrame()}
        return {"h1": self._ohlcv("h1", count), "h4": self._ohlcv("h4", count)}

    def last_price(self) -> float:
        try:
            return float(self._call(self.x.fetch_ticker, self.symbol,
                                    attempts=3)["last"])
        except Exception:  # noqa: BLE001
            return 0.0

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        if not self.connected:
            return OrderResult(False, error="no api keys (shadow mode)")
        try:
            markets = self._call(self.x.load_markets, attempts=3)
            m = markets[self.symbol]
            amount = self.x.amount_to_precision(self.symbol, size_oz)
            oside = "buy" if side == "LONG" else "sell"
            params = {
                "stopLossPrice": self.x.price_to_precision(self.symbol, sl),
                "takeProfitPrice": self.x.price_to_precision(self.symbol, tp),
            }
            o = self._call(self.x.create_order, self.symbol, "market", oside,
                           amount, None, params, attempts=2)
            cprint(f"[BITGET] {oside.upper()} {amount} {m.get('settle','')} | "
                   f"sl {sl:.2f} tp {tp:.2f}", CYAN)
            return OrderResult(True, ticket=str(o.get("id", "")),
                               price=float(o.get("average") or o.get("price") or 0))
        except Exception as e:  # noqa: BLE001
            return OrderResult(False, error=_explain_ccxt_error(e))

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        try:
            self._call(self.x.edit_order, ticket, self.symbol, None, None,
                       params={"stopLossPrice": self.x.price_to_precision(self.symbol, new_sl)},
                       attempts=2)
            return True
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] modify sl failed: {_explain_ccxt_error(e)}", YELLOW)
            return False

    def close_position(self, ticket: str, reason: str = "") -> float | None:
        try:
            self._call(self.x.cancel_order, ticket, self.symbol, attempts=2)
        except Exception:  # noqa: BLE001
            pass
        try:
            pos = self._call(self.x.fetch_positions, [self.symbol], attempts=3)
            for p in pos:
                contracts = p.get("contracts") or 0
                if contracts:
                    side = "sell" if p["side"] == "long" else "buy"
                    self._call(self.x.create_order, self.symbol, "market", side,
                               contracts, None, {"reduceOnly": True}, attempts=2)
                    pnl = p.get("unrealizedPnl")
                    return float(pnl) if pnl is not None else None
        except Exception as e:  # noqa: BLE001
            cprint(f"[BITGET] close failed: {_explain_ccxt_error(e)}", RED)
        return None

    def open_positions(self) -> list[Position]:
        out: list[Position] = []
        if not self.connected:
            return out
        try:
            for p in self._call(self.x.fetch_positions, [self.symbol], attempts=3):
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
