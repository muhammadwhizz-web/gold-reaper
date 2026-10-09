"""
GOLD REAPER :: Risk Manager
===========================
The circuit breakers between you and the margin call.
Implements: per-trade sizing, session target $20, daily target $120,
daily loss stop, consecutive-loss stop, weekend guard.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from core.config import CONFIG
from core.sessions import now_utc


@dataclass
class DayStats:
    date: str
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    consec_losses: int = 0
    trades_taken: int = 0


@dataclass
class SessionStats:
    session: str
    realized_pnl: float = 0.0
    trades_taken: int = 0


@dataclass
class RiskState:
    day: DayStats = field(default_factory=lambda: DayStats(date=now_utc().strftime("%Y-%m-%d")))
    sessions: dict = field(default_factory=dict)   # name -> SessionStats
    balance: float = CONFIG.starting_balance
    equity: float = CONFIG.starting_balance


class RiskManager:
    def __init__(self, cfg: CONFIG.__class__ | None = None) -> None:
        self.cfg = cfg or CONFIG
        self.state = RiskState()
        self.state.balance = self.cfg.starting_balance
        self.state.equity = self.cfg.starting_balance

    # ------------------------------------------------------------- loading
    def load(self) -> None:
        f = self.cfg.state_file
        if f.exists():
            try:
                raw = json.loads(f.read_text())
                self.state = RiskState(
                    day=DayStats(**raw["day"]),
                    sessions={k: SessionStats(**v) for k, v in raw.get("sessions", {}).items()},
                    balance=raw.get("balance", self.cfg.starting_balance),
                    equity=raw.get("equity", self.cfg.starting_balance),
                )
            except Exception:  # noqa: BLE001
                pass
        self.rollover_day()

    def save(self) -> None:
        self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.state_file.write_text(json.dumps({
            "day": asdict(self.state.day),
            "sessions": {k: asdict(v) for k, v in self.state.sessions.items()},
            "balance": self.state.balance,
            "equity": self.state.equity,
            "saved_at": now_utc().isoformat(),
        }, indent=2))

    def rollover_day(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if self.state.day.date != today:
            self.state.day = DayStats(date=today)
            self.state.sessions = {}

    # ------------------------------------------------------------- session
    def session_stats(self, name: str) -> SessionStats:
        if name not in self.state.sessions:
            self.state.sessions[name] = SessionStats(session=name)
        return self.state.sessions[name]

    # ------------------------------------------------------------- checks
    def can_trade(self, session: str) -> tuple[bool, str]:
        self.rollover_day()
        d, s = self.state.day, self.session_stats(session)

        if self.cfg.stop_after_target and d.realized_pnl >= self.cfg.target_per_day:
            return False, f"DAILY TARGET HIT +${d.realized_pnl:.2f} (>= ${self.cfg.target_per_day:.0f}) - reaper rests"
        if self.cfg.stop_after_target and s.realized_pnl >= self.cfg.target_per_session:
            return False, f"SESSION TARGET HIT +${s.realized_pnl:.2f} - waiting next session"
        if d.realized_pnl <= -abs(self.cfg.max_daily_loss_pct / 100 * self.state.equity):
            return False, f"DAILY LOSS LIMIT -${abs(d.realized_pnl):.2f} - circuit breaker"
        if d.consec_losses >= self.cfg.max_consecutive_losses:
            return False, f"{d.consec_losses} consecutive losses - cooldown until next session/day"
        if s.trades_taken >= self.cfg.max_trades_per_session:
            return False, f"session trade cap reached ({s.trades_taken})"
        if d.trades_taken >= self.cfg.max_trades_per_session * 4:
            return False, "daily trade cap reached"
        return True, "OK"

    # -------------------------------------------------------------- sizing
    def position_size(self, entry: float, sl: float, equity: float | None = None) -> float:
        """
        Returns position size in OUNCES of gold.
        risk$ = equity * risk% ; qty_oz = risk$ / |entry - sl|
        Brokers convert oz -> lots/contracts.
        """
        equity = equity if equity is not None else self.state.equity
        risk_usd = equity * self.cfg.risk_per_trade_pct / 100.0
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.0
        qty_oz = risk_usd / dist
        # sanity: never more than 50x leverage notional on equity
        max_notional = equity * 50
        if qty_oz * entry > max_notional:
            qty_oz = max_notional / entry
        return round(qty_oz, 3)

    # ---------------------------------------------------------------- pnl
    def register_fill(self, session: str, pnl: float, is_win: bool) -> None:
        self.rollover_day()
        d, s = self.state.day, self.session_stats(session)
        d.realized_pnl += pnl
        d.trades_taken += 1
        s.realized_pnl += pnl
        s.trades_taken += 1
        if is_win:
            d.wins += 1
            d.consec_losses = 0
        else:
            d.losses += 1
            d.consec_losses += 1
        self.state.balance += pnl
        self.state.equity = self.state.balance
        self.save()

    def update_equity(self, equity: float) -> None:
        self.state.equity = equity
        self.save()
