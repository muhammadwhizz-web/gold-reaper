"""
GOLD REAPER :: Configuration
============================
Every number the bot lives and dies by. Edit .env to override.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _b(key: str, default: bool) -> bool:
    v = os.getenv(key, str(default)).strip().lower()
    return v in ("1", "true", "yes", "on")


@dataclass
class Config:
    # ── identity ─────────────────────────────────────────────────────
    bot_name: str = "GOLD-REAPER"

    # ── broker ───────────────────────────────────────────────────────
    broker: str = os.getenv("BROKER", "PAPER").upper()         # MT5 (Exness) | BITGET | PAPER
    paper: bool = _b("PAPER_MODE", True)                       # ALWAYS default to paper!

    # Exness / MetaTrader 5
    mt5_login: int = _i("MT5_LOGIN", 0)
    mt5_password: str = os.getenv("MT5_PASSWORD", "")
    mt5_server: str = os.getenv("MT5_SERVER", "Exness-MT5Trial")  # e.g. Exness-MT5Real8
    mt5_path: str = os.getenv("MT5_PATH", "")                  # custom terminal64.exe path
    mt5_symbol: str = os.getenv("MT5_SYMBOL", "XAUUSD")

    # Bitget
    bitget_key: str = os.getenv("BITGET_KEY", "")
    bitget_secret: str = os.getenv("BITGET_SECRET", "")
    bitget_passphrase: str = os.getenv("BITGET_PASSPHRASE", "")
    bitget_symbol: str = os.getenv("BITGET_SYMBOL", "XAUT/USDT:USDT")
    bitget_leverage: int = _i("BITGET_LEVERAGE", 3)

    # ── capital & risk (LIVE BY THESE OR DIE BY THEM) ────────────────
    starting_balance: float = _f("STARTING_BALANCE", 1000.0)
    risk_per_trade_pct: float = _f("RISK_PER_TRADE_PCT", 1.0)   # % of equity per trade
    max_daily_loss_pct: float = _f("MAX_DAILY_LOSS_PCT", 3.0)   # hard daily stop
    max_consecutive_losses: int = _i("MAX_CONSEC_LOSSES", 3)
    max_open_trades: int = _i("MAX_OPEN_TRADES", 1)
    max_trades_per_session: int = _i("MAX_TRADES_PER_SESSION", 4)

    # ── profit hunt targets (user mission) ───────────────────────────
    target_per_session: float = _f("TARGET_PER_SESSION_USD", 20.0)   # $20 / 4h session
    target_per_day: float = _f("TARGET_PER_DAY_USD", 120.0)          # $120 / day
    stop_after_target: bool = _b("STOP_AFTER_TARGET", True)

    # ── strategy: REAPER-X ───────────────────────────────────────────
    htf_trend_ema_fast: int = _i("HTF_EMA_FAST", 50)
    htf_trend_ema_slow: int = _i("HTF_EMA_SLOW", 200)
    ema_pullback: int = _i("EMA_PULLBACK", 20)
    rsi_period: int = _i("RSI_PERIOD", 14)
    rsi_long_zone: tuple = (38.0, 52.0)      # pullback reset zone for longs
    rsi_short_zone: tuple = (48.0, 62.0)     # rally reset zone for shorts
    atr_period: int = _i("ATR_PERIOD", 14)
    min_adx: float = _f("MIN_ADX", 24.0)               # trend strength gate (walk-forward)
    sl_atr_mult: float = _f("SL_ATR_MULT", 1.2)
    tp_r: float = _f("TP_R", 2.0)                      # TP distance in R (v2.3 walk-forward)
    tp_atr_mult: float = _f("TP_ATR_MULT", 2.4)        # legacy alias (2.0R x 1.2 ATR)
    entry_hours_utc: tuple = tuple(                    # v2.3 hardened hunt window
        int(x) for x in os.getenv("ENTRY_HOURS_UTC", "12,13").split(",")
        if x.strip().isdigit())
    meanrev_vol_max: float = _f("MEANREV_VOL_MAX", 0.4)  # ATR-rank ceiling for meanrev
    breakeven_at_r: float = _f("BREAKEVEN_AT_R", 1.0)
    trail_start_r: float = _f("TRAIL_START_R", 1.5)
    trail_atr_mult: float = _f("TRAIL_ATR_MULT", 1.2)
    min_atr_price: float = _f("MIN_ATR_PRICE", 3.0)   # skip dead markets (gold $ moves)

    # ── sessions (UTC) ───────────────────────────────────────────────
    trade_asia: bool = _b("TRADE_ASIA", False)
    trade_london: bool = _b("TRADE_LONDON", False)
    trade_overlap: bool = _b("TRADE_OVERLAP", True)
    trade_newyork: bool = _b("TRADE_NEWYORK", False)
    blackout_hours_utc: tuple = ((12, 25), (12, 35), (13, 25), (13, 35))  # US data bombs
    friday_last_entry_utc: int = _i("FRIDAY_LAST_ENTRY_UTC", 18)
    no_entry_minutes_before_weekend_close: int = 120

    # ── runtime ──────────────────────────────────────────────────────
    poll_seconds: int = _i("POLL_SECONDS", 60)
    heartbeat_minutes: int = _i("HEARTBEAT_MINUTES", 30)
    state_file: Path = ROOT / "data" / "state.json"
    trades_file: Path = ROOT / "data" / "trades.csv"
    log_file: Path = ROOT / "data" / "reaper.log"
    tz: str = "UTC"

    # ── derived ──────────────────────────────────────────────────────
    @property
    def session_windows(self) -> dict[str, tuple[int, int] | None]:
        return {
            "ASIA": (0, 7) if self.trade_asia else None,
            "LONDON": (7, 12) if self.trade_london else None,
            "LONDON_NY_OVERLAP": (12, 16) if self.trade_overlap else None,
            "NEWYORK": (16, 21) if self.trade_newyork else None,
            "LATE_US": None,
        }


CONFIG = Config()
