"""
GOLD REAPER :: Paper Broker
===========================
Full simulation with realistic spread + slippage. Zero risk, full behavior.
Uses Yahoo live hourly data so strategy logic is exercised end-to-end.

INSTRUMENT HONESTY (P1-A): the feed is Yahoo ``GC=F`` — CME gold FUTURES —
configured via ``PAPER_INSTRUMENT`` (default GC=F). This is NOT XAU/USD
spot. Prices track spot closely but carry futures basis, and the session
calendar is COMEX's. Nothing here pretends to be spot. The bot's paper
mode is a strategy-execution simulator, not a spot-price oracle.

REPAIR CONTRACT (branch fix/reliability-v1):
  P0-A  SL/TP are enforced by an independent safety net on every fresh
        bar, even if the strategy loop never acts. A bar that crosses
        BOTH TP and SL is resolved as stop-first (conservative, logged
        EXIT_SL_CONSERVATIVE) — mirrors the frozen strategy's own rule.
        A position can close exactly once; every close records reason,
        exit price, timestamp and net realized PnL.
  P0-B  Account state (cash, open positions, append-only closed trades,
        fees, realized PnL, schema version) persists atomically to
        data/paper_account.json (tmpfile -> fsync -> os.replace) and
        mirrors best-effort into DuckDB. A corrupt or unknown-schema
        file REFUSES to trade — the account is never silently reset.
  P1-A  connect() validates the feed (present / finite / positive /
        fresh-enough bars, >= 20 hourly bars for indicators) and reports
        ConnectionStatus.DEGRADED with a precise reason instead of
        pretending to be alive. No random or hardcoded prices, ever.
"""
from __future__ import annotations

import atexit
import json
import os
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

from brokers.base import BrokerBase, OrderResult, Position
from core.logger import GREEN, RED, YELLOW, cprint

SPREAD = 0.35      # gold spread $ (Exness-like), paid inside the entry fill
SLIPPAGE = 0.05    # exit fee per oz (SL/TP fills are charged this, net PnL)
SCHEMA_VERSION = 1

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ACCOUNT_FILE = DATA_DIR / "paper_account.json"
DUCKDB_FILE = DATA_DIR / "paper.duckdb"

# freshness window: 24h while gold trades, wider across scheduled closures
# (Fri 21:00 -> Sun 22:00 UTC) so a healthy weekend connection is not a
# false DEGRADED. P1-A tests force market-open for the strict 24h rule.
FRESH_MAX_AGE_H = 24.0
FRESH_MAX_AGE_H_WEEKEND = 96.0
MIN_HOURLY_BARS = 20
# paper feed bar period (hourly). A bar stamped T covers [T, T+1h): it can
# stop out a position opened anywhere inside that window. The entry-bar
# extreme ambiguity resolves conservatively, mirroring the frozen
# strategy's SL-first rule (docs/STRATEGY_DEFECTS.md SD-5).
BAR_PERIOD = pd.Timedelta("1h")


class PaperStateError(RuntimeError):
    """Account state is corrupt / unknown / newer than this build.

    The broker refuses to trade. The user must inspect the file printed in
    the message (never silently overwritten)."""


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Crash-safe write: tmpfile in the same dir, fsync, atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


class PaperBroker(BrokerBase):
    name = "PAPER"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.instrument = os.getenv("PAPER_INSTRUMENT", "GC=F")
        self.status = ConnectionStatus.DEGRADED
        self.status_reason = "not connected yet"
        self._load_error: str | None = None
        self.balance_val: float = float(cfg.starting_balance)
        self.starting_balance: float = float(cfg.starting_balance)
        self.fees_paid: float = 0.0
        self.realized_pnl: float = 0.0
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[dict] = []       # append-only ledger
        self._closed_tickets: set[str] = set()    # exactly-once guard
        self._mirrored: int = 0                   # duckdb mirror cursor
        self._mirror_warned = False
        self._cache: tuple[str, dict[str, pd.DataFrame]] | None = None
        self._cache_ts: float = 0.0
        self._last_px: float = 0.0   # last seen feed price (no re-fetch)
        self._last_stop_ts: pd.Timestamp | None = None
        # P0-B: paths are bound AT CONSTRUCTION so late writers (the
        # atexit flush) can never follow a module-global that someone
        # re-pointed mid-run (this exact race once leaked test state
        # into the real data/ directory).
        self.account_path = Path(ACCOUNT_FILE)
        self.duckdb_path = Path(DUCKDB_FILE)
        self._load_state()
        atexit.register(self._atexit_save)

    # ----------------------------------------------------------- P0-B state
    def _account_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "starting_balance": self.starting_balance,
            "cash_balance": round(self.balance_val, 6),
            "equity": round(self._equity_quiet(), 6),
            "fees_paid": round(self.fees_paid, 6),
            "realized_pnl": round(self.realized_pnl, 6),
            "open_positions": [self._pos_dict(p) for p in
                               self.positions.values() if not p.meta.get("closed")],
            "closed_trades": self.closed_trades,
            "updated_at": _utcnow().isoformat(),
        }

    @staticmethod
    def _pos_dict(p: Position) -> dict:
        d = asdict(p)
        d["opened_at"] = p.opened_at.isoformat() if p.opened_at else None
        return d

    def _load_state(self) -> None:
        """Load persisted state. NEVER silently resets an existing account."""
        if not self.account_path.exists():
            cprint(f"[PAPER] no account state at {self.account_path} - "
                   f"initialized fresh at ${self.starting_balance:,.2f}", YELLOW)
            self._save()
            return
        try:
            raw = json.loads(self.account_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # corrupt: back up, then refuse to trade until user confirms
            bak = self.account_path.with_suffix(".json.bak")
            try:
                bak.write_bytes(ACCOUNT_FILE.read_bytes())
                cprint(f"[PAPER] corrupt account state backed up -> {bak}", RED)
            except OSError:
                cprint("[PAPER] corrupt account state, backup FAILED", RED)
            raise PaperStateError(
                f"paper account state is corrupt: {self.account_path} ({e}). "
                f"Backup written to {bak}. Inspect/restore it, then run "
                f"`python cli.py doctor --fix` to confirm. The bot will "
                f"not overwrite your account silently.") from e
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise PaperStateError(
                f"paper account state {self.account_path} has schema_version="
                f"{version!r}, this build speaks {SCHEMA_VERSION}. "
                f"Refusing to start (file left untouched).")
        try:
            self.starting_balance = float(raw.get(
                "starting_balance", self.cfg.starting_balance))
            self.balance_val = float(raw.get("cash_balance",
                                             self.starting_balance))
            self.fees_paid = float(raw.get("fees_paid", 0.0))
            self.realized_pnl = float(raw.get("realized_pnl", 0.0))
            for d in raw.get("open_positions", []):
                pos = self._pos_from_dict(d)
                if pos is not None and not pos.meta.get("closed"):
                    self.positions[pos.ticket] = pos
            self.closed_trades = list(raw.get("closed_trades", []))
            self._closed_tickets = {str(t["ticket"])
                                    for t in self.closed_trades
                                    if t.get("ticket")}
            self._mirrored = len(self.closed_trades)
        except (TypeError, ValueError, KeyError) as e:
            raise PaperStateError(
                f"paper account state {self.account_path} is unreadable "
                f"({e}). Refusing to trade on a half-loaded account.") from e
        if self.positions or self.closed_trades:
            cprint(f"[PAPER] restored account | cash ${self.balance_val:,.2f} | "
                   f"{len(self.positions)} open | "
                   f"{len(self.closed_trades)} closed trades", GREEN)

    @staticmethod
    def _pos_from_dict(d: dict) -> Position | None:
        try:
            opened = d.get("opened_at")
            opened_dt = (datetime.fromisoformat(str(opened))
                         if opened else _utcnow())
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            return Position(
                ticket=str(d["ticket"]), side=str(d["side"]),
                size_oz=float(d["size_oz"]), entry=float(d["entry"]),
                sl=float(d["sl"]), tp=float(d["tp"]),
                opened_at=opened_dt,
                be_moved=bool(d.get("be_moved", False)),
                trail_active=bool(d.get("trail_active", False)),
                orig_sl=float(d.get("orig_sl", d["sl"])),
                session=str(d.get("session", "")),
                reason=str(d.get("reason", "")),
                meta=dict(d.get("meta", {}) or {}),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _save(self) -> None:
        try:
            _atomic_write_json(self.account_path, self._account_dict())
        except OSError as e:
            cprint(f"[PAPER] account state save failed: {e}", RED)
        self._mirror_duckdb()

    def heartbeat_save(self) -> None:
        """60s liveness flush (bot tick calls this)."""
        self._save()

    def _atexit_save(self) -> None:
        if self._load_error is None:
            try:
                _atomic_write_json(self.account_path, self._account_dict())
            except Exception:  # noqa: BLE001 - last-gasp flush
                pass

    def _mirror_duckdb(self) -> None:
        """Best-effort analytical mirror. Disk JSON stays the source of
        truth; a missing/broken duckdb only warns once."""
        try:
            import duckdb
            con = duckdb.connect(str(self.duckdb_path))
            try:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS paper_trades ("
                    "ticket VARCHAR, side VARCHAR, size_oz DOUBLE, "
                    "entry DOUBLE, exit_price DOUBLE, reason VARCHAR, "
                    "exit_ts VARCHAR, pnl DOUBLE, fee DOUBLE)")
                for rec in self.closed_trades[self._mirrored:]:
                    con.execute(
                        "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [rec.get("ticket"), rec.get("side"),
                         float(rec.get("size_oz", 0)),
                         float(rec.get("entry", 0)),
                         float(rec.get("exit_price", 0)),
                         str(rec.get("reason", "")),
                         str(rec.get("exit_ts", "")),
                         float(rec.get("pnl", 0)),
                         float(rec.get("fee", 0))])
                self._mirrored = len(self.closed_trades)
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001 - mirror must never trade-block
            if not self._mirror_warned:
                self._mirror_warned = True
                cprint(f"[PAPER] duckdb mirror unavailable ({e}) - "
                       f"JSON remains the source of truth", YELLOW)

    # ------------------------------------------------------------- data
    def _yahoo(self, period: str, interval: str) -> pd.DataFrame:
        import yfinance as yf
        df = yf.download(self.instrument, period=period, interval=interval,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "time"
        return df

    def candles(self, timeframe: str, count: int) -> dict[str, pd.DataFrame]:
        import time as _t
        now = _t.time()
        if self._cache and now - self._cache_ts < 120:
            cached = self._cache[1]
            return {"h1": cached["h1"].tail(count), "h4": cached["h4"].tail(count)}
        h1 = self._yahoo("60d", "1h")
        h4 = h1.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna() if not h1.empty else pd.DataFrame()
        self._cache = (str(now), {"h1": h1, "h4": h4})
        self._cache_ts = now
        # P0-A independent safety net: fresh bars may cross SL/TP even if
        # the strategy loop is down / missed it
        self.enforce_stops(h1)
        return {"h1": h1.tail(count), "h4": h4.tail(count)}

    def last_price(self) -> float:
        h1 = self.candles("h1", 2)["h1"]
        px = float(h1["close"].iloc[-1]) if not h1.empty else 0.0
        if px:
            self._last_px = px
        return px

    # ------------------------------------------------------------- P1-A feed
    def _max_fresh_age(self) -> timedelta:
        try:
            from core.sessions import is_market_open
            hours = FRESH_MAX_AGE_H if is_market_open() \
                else FRESH_MAX_AGE_H_WEEKEND
        except Exception:  # noqa: BLE001
            hours = FRESH_MAX_AGE_H
        return timedelta(hours=hours)

    @staticmethod
    def _bars_valid(df: pd.DataFrame, n: int = 3) -> str | None:
        """None = ok, else the precise reason the bars are unusable."""
        if df is None or df.empty:
            return "feed returned no bars"
        tail = df.tail(n)
        for col in ("open", "high", "low", "close"):
            if col not in tail.columns:
                return f"feed bars missing column '{col}'"
            vals = tail[col].to_numpy(dtype=float, na_value=float("nan"))
            if not len(vals):
                return f"feed column '{col}' has no values"
            if not all(pd.notna(v) for v in vals):
                return f"feed has NaN {col} in the last {len(vals)} bars"
            if any(v <= 0 for v in vals):
                return f"feed has non-positive {col}"
        return None

    def connect(self) -> bool:
        """Validate the price feed before claiming 'connected' (P1-A)."""
        self.status = ConnectionStatus.CONNECTED
        self.status_reason = ""
        try:
            daily = self._yahoo("7d", "1d")
            reason = self._bars_valid(daily, n=3)
            if reason:
                self.status = ConnectionStatus.DEGRADED
                self.status_reason = f"daily bars: {reason}"
            else:
                last_ts = daily.index[-1]
                if last_ts.tzinfo is None:
                    last_ts = last_ts.tz_localize("UTC")
                age = _utcnow() - last_ts.to_pydatetime()
                if age > self._max_fresh_age():
                    self.status = ConnectionStatus.DEGRADED
                    self.status_reason = (
                        f"stale feed: last daily bar {age.total_seconds()/3600:.1f}h "
                        f"old (limit {self._max_fresh_age().total_seconds()/3600:.0f}h)")
            if self.status == ConnectionStatus.CONNECTED:
                h1 = self._yahoo("7d", "1h")
                if h1.empty or len(h1) < MIN_HOURLY_BARS:
                    self.status = ConnectionStatus.DEGRADED
                    self.status_reason = (
                        f"only {0 if h1 is None else len(h1)} hourly bars - "
                        f"need >= {MIN_HOURLY_BARS} for indicators")
                else:
                    reason = self._bars_valid(h1, n=3)
                    if reason:
                        self.status = ConnectionStatus.DEGRADED
                        self.status_reason = f"hourly bars: {reason}"
        except Exception as e:  # noqa: BLE001 - validation must classify, not die
            self.status = ConnectionStatus.DEGRADED
            self.status_reason = f"feed error: {type(e).__name__}: {e}"

        if self.status == ConnectionStatus.CONNECTED:
            cprint(f"[PAPER] armed | instrument {self.instrument} (CME gold "
                   f"futures via Yahoo - NOT XAU/USD spot) | virtual balance "
                   f"${self.balance_val:,.2f} | spread ${SPREAD} | "
                   f"exit fee ${SLIPPAGE}/oz", GREEN)
            return True
        cprint(f"[PAPER] DEGRADED: {self.status_reason}", RED)
        return False

    def disconnect(self) -> None:
        # P0-B: flush state on orderly shutdown (bot SIGTERM path)
        self._save()

    # ------------------------------------------------------------- account
    def balance(self) -> float:
        return self.balance_val

    def equity(self) -> float:
        eq = self.balance_val
        px = self._last_px or self.last_price()
        for p in self.positions.values():
            if px:
                eq += self._unrealized(p, px)
        return eq

    def _equity_quiet(self) -> float:
        """Equity WITHOUT a network fetch (persistence path). Uses the
        last seen price; before any feed data it equals cash."""
        eq = self.balance_val
        px = self._last_px
        for p in self.positions.values():
            if px:
                eq += self._unrealized(p, px)
        return eq

    def _unrealized(self, p: Position, px: float) -> float:
        diff = (px - p.entry) if p.side == "LONG" else (p.entry - px)
        return diff * p.size_oz

    # ------------------------------------------------------------- trading
    def market_order(self, side: str, size_oz: float, sl: float, tp: float,
                     session: str, reason: str) -> OrderResult:
        px = self.last_price()
        if not px:
            return OrderResult(False, error="no price feed")
        fill = px + (SPREAD + SLIPPAGE) if side == "LONG" else px - (SPREAD + SLIPPAGE)
        ticket = uuid.uuid4().hex[:10]
        self.positions[ticket] = Position(
            ticket=ticket, side=side, size_oz=size_oz, entry=fill, sl=sl, tp=tp,
            opened_at=datetime.now(timezone.utc), orig_sl=sl, session=session,
            reason=reason,
        )
        cprint(f"[PAPER] {side} {size_oz:.3f} oz @ {fill:.2f} | sl {sl:.2f} tp {tp:.2f}",
               GREEN)
        self._save()  # P0-B: persist on every state change
        return OrderResult(True, ticket=ticket, price=fill)

    def modify_sl(self, ticket: str, new_sl: float) -> bool:
        if ticket in self.positions:
            self.positions[ticket].sl = new_sl
            self._save()  # P0-B: SL moves are state changes too
            return True
        return False

    # ------------------------------------------------------------- closing
    def close_position(self, ticket: str, reason: str = "",
                       intended_price: float | None = None) -> float | None:
        """Close exactly once. PnL is net of the exit fee and, when an
        intended price is given (SL/TP from the strategy or safety net),
        fills AT that price - never at a flattering last price."""
        if ticket in self._closed_tickets:
            cprint(f"[PAPER] dedupe: {ticket} already closed - ignoring "
                   f"duplicate close ({reason})", YELLOW)
            return None
        p = self.positions.pop(ticket, None)
        if not p:
            return None
        self._closed_tickets.add(ticket)
        p.meta["closed"] = True
        p.meta["closed_at"] = _utcnow().isoformat()
        if intended_price is not None:
            exit_px = float(intended_price)
        else:
            px = self.last_price()
            if not px:
                # cannot price an honest exit -> restore, refuse to close
                self.positions[ticket] = p
                self._closed_tickets.discard(ticket)
                p.meta.pop("closed", None)
                p.meta.pop("closed_at", None)
                cprint("[PAPER] close refused: no price to fill against", RED)
                return None
            exit_px = px - SLIPPAGE if p.side == "LONG" else px + SLIPPAGE
        return self._realize_close(p, exit_px, reason or "MANUAL")

    def _realize_close(self, p: Position, exit_px: float, reason: str) -> float:
        fee = SLIPPAGE * p.size_oz
        pnl = self._unrealized(p, exit_px) - fee
        self.balance_val += pnl
        self.fees_paid += fee
        self.realized_pnl += pnl
        self.closed_trades.append({
            "ticket": p.ticket, "side": p.side, "size_oz": p.size_oz,
            "entry": round(p.entry, 4), "exit_price": round(exit_px, 4),
            "reason": reason, "exit_ts": _utcnow().isoformat(),
            "pnl": round(pnl, 6), "fee": round(fee, 6),
        })
        cprint(f"[PAPER] closed {p.side} {p.ticket} @ {exit_px:.2f} "
               f"({reason}) pnl {pnl:+.2f} | balance ${self.balance_val:,.2f}",
               GREEN if pnl >= 0 else RED)
        self._save()  # P0-B: append-only trade ledger persisted immediately
        return pnl

    # ------------------------------------------------- P0-A safety net
    def enforce_stops(self, bars: pd.DataFrame | None) -> int:
        """Independent SL/TP enforcement over fresh bars.

        Runs even if the strategy loop never calls manage_exit. A bar that
        crosses BOTH TP and SL resolves stop-first (conservative, mirrors
        the frozen strategy rule in core/strategy.py::manage_exit).
        Returns the number of closes executed."""
        if bars is None or bars.empty or not self.positions:
            return 0
        try:
            frame = bars.sort_index()
            if self._last_stop_ts is not None:
                frame = frame[frame.index > self._last_stop_ts]
            if frame.empty:
                return 0
            closes = 0
            for p in list(self.positions.values()):
                if p.meta.get("closed"):
                    continue
                for ts, bar in frame.iterrows():
                    if isinstance(ts, pd.Timestamp) and \
                            ts.tz_convert("UTC").to_pydatetime() + \
                            BAR_PERIOD <= p.opened_at:
                        continue  # bar closed entirely before the entry
                    hi, lo = float(bar["high"]), float(bar["low"])
                    if p.side == "LONG":
                        hit_sl, hit_tp = lo <= p.sl, hi >= p.tp
                    else:
                        hit_sl, hit_tp = hi >= p.sl, lo <= p.tp
                    if hit_sl and hit_tp:
                        self.close_position(p.ticket, reason="EXIT_SL_CONSERVATIVE",
                                            intended_price=p.sl)
                        break
                    if hit_sl:
                        self.close_position(p.ticket, reason="SL",
                                            intended_price=p.sl)
                        break
                    if hit_tp:
                        self.close_position(p.ticket, reason="TP",
                                            intended_price=p.tp)
                        break
                closes += 1 if p.meta.get("closed") else 0
            self._last_stop_ts = frame.index[-1]
            return closes
        except Exception as e:  # noqa: BLE001 - net must not kill the loop
            cprint(f"[PAPER] safety-net error: {e}", RED)
            return 0

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if not p.meta.get("closed")]
