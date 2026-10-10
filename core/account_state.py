"""
GOLD REAPER :: Account State (single source of truth)
=====================================================
REPAIR CONTRACT (branch fix/reliability-v1, P0-C):

Every risk surface — legacy RiskManager (day/session ledgers) and the
APEX survival layer (blocks/breakers) — reads balance, equity, realized
PnL, daily PnL and the loss streak from ONE canonical AccountState
object that bot.py owns and persists to data/account_state.json.

Design notes:
  - core/risk.py and core/risk_apex.py are FROZEN (docs/
    PROTECTED_HASHES.txt): this module unifies their *inputs* from the
    outside. bot.py hydrates both managers from the same state after
    every fill and every equity sync, and routes every fill event into
    both of them — the two ledgers can no longer drift.
  - Persisted atomically (tmpfile -> fsync -> os.replace) so a crash
    mid-write can never corrupt the risk truth.
  - Unknown/corrupt state refuses to start (same philosophy as the
    paper account: never silently reset money state).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_STATE_FILE = ROOT / "data" / "account_state.json"
SCHEMA_VERSION = 1


class AccountStateError(RuntimeError):
    """Canonical risk state is corrupt or from a newer schema - refuse."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


@dataclass
class AccountState:
    """The one money-truth every risk gate shares."""
    starting_balance: float = 1000.0
    balance: float = 1000.0
    equity: float = 1000.0
    realized_pnl: float = 0.0        # total, all time
    day_date: str = ""               # UTC yyyy-mm-dd of day_pnl
    day_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    consec_losses: int = 0
    fees_paid: float = 0.0
    updated_at: str = ""
    schema_version: int = SCHEMA_VERSION
    extra: dict = field(default_factory=dict)   # future-proofing

    # ------------------------------------------------------------ streaks
    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100.0) if self.trades else 0.0

    # ------------------------------------------------------------ mutations
    def rollover_day(self, today: str | None = None) -> bool:
        """UTC day roll: day_pnl resets, streaks survive (they are
        psychological, not calendar). Returns True on a rollover."""
        today = today or _utcnow().strftime("%Y-%m-%d")
        if self.day_date == today:
            return False
        self.day_date = today
        self.day_pnl = 0.0
        return True

    def register_fill(self, pnl: float, is_win: bool,
                      fees: float = 0.0) -> None:
        """One realized fill: the only mutation path for money truth."""
        self.rollover_day()
        self.realized_pnl += pnl
        self.day_pnl += pnl
        self.balance += pnl
        self.equity = self.balance
        self.fees_paid += fees
        if is_win:
            self.wins += 1
            self.consec_losses = 0
        else:
            self.losses += 1
            self.consec_losses += 1

    def sync_equity(self, equity: float) -> None:
        """Mark-to-market refresh; realized truth stays untouched."""
        if equity and equity > 0:
            self.equity = float(equity)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        d = asdict(self)
        d["updated_at"] = _utcnow().isoformat()
        return d

    def save(self, path: Path | str | None = None) -> None:
        _atomic_write(Path(path) if path else ACCOUNT_STATE_FILE,
                      self.to_dict())

    @classmethod
    def load(cls, starting_balance: float = 1000.0,
             path: Path | None = None) -> "AccountState":
        """Load persisted truth. Missing -> fresh (logged by caller).
        Corrupt/unknown -> refuse (never silently reset money state)."""
        p = Path(path or ACCOUNT_STATE_FILE)
        if not p.exists():
            st = cls(starting_balance=starting_balance, balance=starting_balance,
                     equity=starting_balance)
            st.rollover_day()
            return st
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise AccountStateError(
                f"account state {p} is corrupt ({e}). Restore or remove it "
                f"manually - the bot will not silently reset risk state.") from e
        if raw.get("schema_version") not in (None, SCHEMA_VERSION):
            raise AccountStateError(
                f"account state {p} has schema_version="
                f"{raw.get('schema_version')!r}, this build speaks "
                f"{SCHEMA_VERSION}. Refusing to start.")
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        try:
            st = cls(
                starting_balance=float(raw.get("starting_balance",
                                               starting_balance)),
                balance=float(raw.get("balance", starting_balance)),
                equity=float(raw.get("equity", starting_balance)),
                realized_pnl=float(raw.get("realized_pnl", 0.0)),
                day_date=str(raw.get("day_date", "")),
                day_pnl=float(raw.get("day_pnl", 0.0)),
                wins=int(raw.get("wins", 0)),
                losses=int(raw.get("losses", 0)),
                consec_losses=int(raw.get("consec_losses", 0)),
                fees_paid=float(raw.get("fees_paid", 0.0)),
                updated_at=str(raw.get("updated_at", "")),
                extra=dict(raw.get("extra", {}) or {}),
            )
        except (TypeError, ValueError) as e:
            raise AccountStateError(
                f"account state {p} has unreadable fields ({e}). "
                f"Refusing to trade on half-loaded risk state.") from e
        del known
        st.rollover_day()
        return st
