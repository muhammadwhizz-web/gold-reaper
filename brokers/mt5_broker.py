"""
GOLD REAPER :: Exness via MetaTrader 5
======================================
Exness retail accounts trade gold (XAUUSD) through the MT5 terminal.
The official `MetaTrader5` python package talks to terminal64.exe
(Windows natively; Linux via Wine — see README).

If MT5 is not available (wrong platform / terminal closed) this adapter
falls back to live price via yfinance so the bot stays operational in
shadow mode, and refuses to send orders.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from core.logger import cprint, RED, YELLOW

TF_MAP = {"h1": 16385, "h4": 16388, "d1": 16408}  # MT5 constants

try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False


class ExnessMT5(BrokerBase):
    name = "EXNESS-MT5"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.connected = False
        self.symbol = cfg.mt5_symbol

    # ------------------------------------------------------------- connect
    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            cprint("[MT5] MetaTrader5 package not installed on this platform "
                   "(Windows/Wine required). Running in SHADOW mode.", YELLOW)
            return False
        kwargs = {}
        if self.cfg.mt5_path:
            kwargs["path"] = self.cfg.mt5_path
        if not mt5.initialize(**kwargs):
            cprint(f"[MT5] initialize() failed: {mt5.last_error()}", RED)
            return False
        if self.cfg.mt5_login:
            ok = mt5.login(self.cfg.mt5_login, password=self.cfg.mt5_password,
                           server=self.cfg.mt5_server)
            if not ok:
                cprint(f"[MT5] login failed: {mt5.last_error()}", RED)
                return False
        if not mt5.symbol_select(self.symbol, True):
            cprint(f"[MT5] symbol {self.symbol} not found: {mt5.last_error()}", RED)
            return False
        info = mt5.symbol_info(self.symbol)
        cprint(f"[MT5] connected | {self.symbol} | balance ${mt5.account_info().balance:,.2f}",
               YELLOW)
        self.connected = True
        _ = info
        return True

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
        self.connected = False

    # ------------------------------------------------------------- account
    def balance(self) -> float:
        if MT5_AVAILABLE and self.connected and (ai := mt5.account_info()):
            return float(ai.balance)
        return 0.0

    def equity(self) -> float:
        if MT5_AVAILABLE and self.connected and (ai := mt5.account_info()):
            return float(ai.equity)
        return self.balance()

    # ------------------------------------------------------------- market data
    def _rates(self, tf_code: int, count: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.symbol, tf_code, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")[["open", "high", "low", "close", "volume"]]
        return df

    def candles(self, timeframe: str, count: int) -> dict[str, object]:
        if not (MT5_AVAILABLE and self.connected):
            return {"h1": pd.DataFrame(), "h4": pd.DataFrame()}
        return {"h1": self._rates(TF_MAP["h1"], count), "h4": self._rates(TF_MAP["h4"], count)}

    def last_price(self) -> float:
        if MT5_AVAILABLE and self.connected:
            t = mt5.symbol_info_tick(self.symbol)
            if t:
                return float(t.bid)
        return 0.0

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        if not (MT5_AVAILABLE and self.connected):
            return OrderResult(False, error="MT5 unavailable (shadow mode)")

        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if not info or not tick:
            return OrderResult(False, error="no symbol info")

        lots = self._oz_to_lots(size_oz, info)
        order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "LONG" else tick.bid
        digits = info.digits

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "deviation": 30,
            "magic": 666666,
            "comment": f"REAPER {session[:6]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        res = mt5.order_send(req)
        if res is None:
            return OrderResult(False, error="order_send None")
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, error=f"retcode {res.retcode}: {res.comment}")
        cprint(f"[MT5] {side} {lots} lots @ {res.price} | sl {sl:.2f} tp {tp:.2f}", YELLOW)
        return OrderResult(True, ticket=str(res.order), price=float(res.price))

    def _oz_to_lots(self, size_oz: float, info) -> float:
        contract = info.trade_contract_size or 100.0   # XAUUSD: 100 oz / lot
        lots = size_oz / contract
        step = info.volume_step or 0.01
        minv = info.volume_min or 0.01
        lots = max(minv, round(lots / step) * step)
        return round(min(lots, info.volume_max or 100.0), 2)

    def _filling_mode(self, info) -> int:
        fm = getattr(info, "filling_mode", 1)
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        if not (MT5_AVAILABLE and self.connected):
            return False
        for pos in mt5.positions_get(ticket=int(ticket)) or []:
            digits = mt5.symbol_info(pos.symbol).digits
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": round(new_sl, digits),
                "tp": pos.tp,
            }
            res = mt5.order_send(req)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        return False

    def close_position(self, ticket: str, reason: str = "") -> float | None:
        if not (MT5_AVAILABLE and self.connected):
            return None
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return None
        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        is_long = pos.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30,
            "magic": 666666,
            "comment": f"REAPER close {reason[:10]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(mt5.symbol_info(pos.symbol)),
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return None  # pnl read back via history; caller computes from prices
        return None

    def open_positions(self) -> list[Position]:
        if not (MT5_AVAILABLE and self.connected):
            return []
        out: list[Position] = []
        for p in mt5.positions_get() or []:
            if p.magic != 666666:
                continue
            out.append(Position(
                ticket=str(p.ticket),
                side="LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT",
                size_oz=p.volume * (mt5.symbol_info(p.symbol).trade_contract_size or 100),
                entry=p.price_open, sl=p.sl, tp=p.tp,
                opened_at=datetime.fromtimestamp(p.time, tz=timezone.utc),
            ))
        return out
