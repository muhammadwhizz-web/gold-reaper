"""
GOLD REAPER APEX :: Risk Engine (the survival layer)
====================================================
Implements Phase 4 engineering exactly:

  - 4-HOUR BLOCK TRACKER   : UTC-aligned 4h blocks; block closes at >= $20
  - ADAPTIVE SIZING        : risk = clamp(0.5%..2%, $20 / (avg_R x equity))
  - TRADE BUDGET           : max 3 attempts per block
  - HIGH-PROBABILITY GATE  : confidence floor passed by the ensemble
  - ROLLING PF MONITOR     : 30d PF < 1.2 -> risk x0.5 until recovered
  - RECOVERY MODE          : losing day -> next blocks at 50% until breakeven
  - CIRCUIT BREAKERS       : day -3% / week -7% / month -15% -> LATCHED stop,
                             manual reset required (bot.py --reset-breakers)
  - COMPOUNDING            : risk scales with live equity, never with hope

State persists to data/apex_risk.json (+ optional Redis mirror) and survives
crashes, reboots and zombie apocalypses.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from core.audit import log_risk
from core.config import CONFIG, Config
from core.logger import RED, YELLOW, cprint

RISK_FILE = CONFIG.state_file.parent / "apex_risk.json"
BREAKER_FILE = CONFIG.state_file.parent / "breakers.json"

BLOCK_TARGET = 20.0
BLOCK_MAX_ATTEMPTS = 3
DAILY_STOP = -0.03
WEEKLY_STOP = -0.07
MONTHLY_STOP = -0.15
PF_FLOOR = 1.2
PF_RECOVER = 1.3
MIN_RISK_PCT = 0.5
MAX_RISK_PCT = 2.0
CONF_FLOOR = 0.55


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def block_key(ts: datetime | None = None) -> str:
    ts = ts or _utcnow()
    return f"{ts:%Y-%m-%dT}{(ts.hour // 4) * 4:02d}"


def week_key(ts: datetime | None = None) -> str:
    ts = ts or _utcnow()
    iso = ts.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key(ts: datetime | None = None) -> str:
    ts = ts or _utcnow()
    return f"{ts:%Y-%m}"


@dataclass
class BlockState:
    key: str
    pnl: float = 0.0
    attempts: int = 0
    done: bool = False


@dataclass
class ApexRiskState:
    current_block: BlockState = field(default_factory=lambda: BlockState(key=block_key()))
    day_date: str = ""
    day_pnl: float = 0.0
    day_start_equity: float = 0.0
    week: str = ""
    week_pnl: float = 0.0
    week_start_equity: float = 0.0
    month: str = ""
    month_pnl: float = 0.0
    month_start_equity: float = 0.0
    equity: float = CONFIG.starting_balance
    balance: float = CONFIG.starting_balance
    in_recovery: bool = False
    risk_multiplier: float = 1.0        # PF monitor multiplier
    wins: int = 0
    losses: int = 0
    consec_losses: int = 0


class ApexRisk:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or CONFIG
        self.state = ApexRiskState()
        self._redis = self._connect_redis()
        self.sync_period_starts()

    # ------------------------------------------------------------- persistence
    def _connect_redis(self):
        url = os.getenv("REDIS_URL", "")
        if not url:
            return None
        try:
            import redis
            return redis.Redis.from_url(url, decode_responses=True)
        except Exception:  # noqa: BLE001
            return None

    def load(self) -> None:
        if RISK_FILE.exists():
            try:
                raw = json.loads(RISK_FILE.read_text())
                self.state = ApexRiskState(
                    current_block=BlockState(**raw["current_block"]),
                    **{k: v for k, v in raw.items() if k != "current_block"})
            except Exception:  # noqa: BLE001
                pass
        self.sync_period_starts()

    def save(self) -> None:
        RISK_FILE.parent.mkdir(parents=True, exist_ok=True)
        RISK_FILE.write_text(json.dumps(asdict(self.state), indent=2))
        if self._redis is not None:
            try:
                self._redis.set("apex:risk", json.dumps(asdict(self.state)))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- periods
    def sync_period_starts(self) -> None:
        now = _utcnow()
        st = self.state
        if st.day_date != now.strftime("%Y-%m-%d"):
            if st.day_pnl < 0:
                st.in_recovery = True
            st.day_date = now.strftime("%Y-%m-%d")
            st.day_pnl = 0.0
            st.day_start_equity = st.equity
        if st.week != week_key(now):
            st.week = week_key(now)
            st.week_pnl = 0.0
            st.week_start_equity = st.equity
        if st.month != month_key(now):
            st.month = month_key(now)
            st.month_pnl = 0.0
            st.month_start_equity = st.equity
        bk = block_key(now)
        if st.current_block.key != bk:
            st.current_block = BlockState(key=bk)
        self.save()

    # ------------------------------------------------------------- breakers
    def _latch(self, reason: str) -> None:
        BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        BREAKER_FILE.write_text(json.dumps({
            "latched": True, "reason": reason, "at": _utcnow().isoformat()}))
        cprint(f"[BREAKER] LATCHED: {reason} — manual reset required", RED)
        log_risk(f"circuit breaker latched: {reason}",
                 {"day_pnl": self.state.day_pnl, "week_pnl": self.state.week_pnl,
                  "month_pnl": self.state.month_pnl})
        try:
            from core.notify import notify
            notify("CIRCUIT BREAKER", reason)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def is_latched() -> tuple[bool, str]:
        if not BREAKER_FILE.exists():
            return False, ""
        try:
            d = json.loads(BREAKER_FILE.read_text())
            return bool(d.get("latched")), str(d.get("reason", ""))
        except Exception:  # noqa: BLE001
            return False, ""

    @staticmethod
    def reset_breakers() -> None:
        if BREAKER_FILE.exists():
            BREAKER_FILE.unlink()
            cprint("[BREAKER] manually reset — reaper may hunt again", YELLOW)

    def _check_breakers(self) -> tuple[bool, str]:
        st = self.state
        if st.day_start_equity > 0 and \
                st.day_pnl / st.day_start_equity <= DAILY_STOP:
            self._latch(f"daily loss {st.day_pnl:+.2f} "
                        f"({st.day_pnl / max(st.day_start_equity, 1) * 100:.1f}%)")
            return True, "daily breaker"
        if st.week_start_equity > 0 and \
                st.week_pnl / st.week_start_equity <= WEEKLY_STOP:
            self._latch(f"weekly loss {st.week_pnl:+.2f}")
            return True, "weekly breaker"
        if st.month_start_equity > 0 and \
                st.month_pnl / st.month_start_equity <= MONTHLY_STOP:
            self._latch(f"monthly loss {st.month_pnl:+.2f}")
            return True, "monthly breaker"
        return False, ""

    # ------------------------------------------------------------- rolling PF
    def rolling_pf(self, trades_csv=None, window_days: int = 30) -> float | None:
        # journal rows carry realized pnl in closes; approximate via state file
        # when the journal lacks pnl (entries only). Uses audit ledger.
        try:
            from core.audit import AUDIT_FILE
            if not AUDIT_FILE.exists():
                return None
            import json as _json
            pnls = []
            cutoff = _utcnow().timestamp() - window_days * 86400
            for line in AUDIT_FILE.read_text().splitlines()[-4000:]:
                try:
                    rec = _json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("kind") != "fill":
                    continue
                ts = rec.get("ts", "")
                try:
                    t = datetime.fromisoformat(ts).timestamp()
                except Exception:  # noqa: BLE001
                    continue
                if t >= cutoff:
                    pnls.append(float(rec["data"].get("pnl", 0)))
            if len(pnls) < 8:
                return None
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            if gl == 0:
                return None
            return gp / gl
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------- gates
    def can_open(self, confidence: float | None = None) -> tuple[bool, str]:
        self.sync_period_starts()
        st = self.state

        latched, why = self.is_latched()
        if latched:
            return False, f"circuit breaker latched: {why}"

        hit, _ = self._check_breakers()
        if hit:
            return False, "circuit breaker"

        if st.current_block.done or st.current_block.pnl >= BLOCK_TARGET:
            st.current_block.done = True
            return False, (f"block target hit +${st.current_block.pnl:.2f} "
                           f"(>= ${BLOCK_TARGET:.0f})")
        if st.current_block.attempts >= BLOCK_MAX_ATTEMPTS:
            return False, f"block attempt budget spent ({st.current_block.attempts})"
        if confidence is not None and confidence < CONF_FLOOR:
            return False, f"confidence {confidence:.2f} < floor {CONF_FLOOR}"
        if st.consec_losses >= self.cfg.max_consecutive_losses:
            return False, f"{st.consec_losses} consecutive losses - cooling"
        return True, "OK"

    # ------------------------------------------------------------- sizing
    def risk_fraction(self, avg_r: float | None = None) -> float:
        """Adaptive: big enough that a clean win banks the $20 block target,
        small enough to survive. avg_R from realized fills (fallback 1.5)."""
        st = self.state
        avg_r = max(avg_r or 1.5, 0.8)
        frac_target = BLOCK_TARGET / (avg_r * max(st.equity, 1.0)) * 100
        frac = min(MAX_RISK_PCT, max(MIN_RISK_PCT, frac_target))
        # PF monitor multiplier
        pf = self.rolling_pf()
        if pf is not None and pf < PF_FLOOR:
            st.risk_multiplier = 0.5
        elif pf is not None and pf >= PF_RECOVER:
            st.risk_multiplier = 1.0
        if st.in_recovery:
            frac *= 0.5
        return frac * st.risk_multiplier

    def position_size_oz(self, entry: float, sl: float,
                         equity: float | None = None) -> tuple[float, float]:
        """Returns (ounces, risk_usd)."""
        st = self.state
        eq = equity if equity is not None else st.equity
        frac = self.risk_fraction()
        risk_usd = eq * frac / 100.0
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.0, 0.0
        oz = risk_usd / dist
        max_notional = eq * 50
        if oz * entry > max_notional:
            oz = max_notional / entry
        return round(oz, 3), risk_usd

    # ------------------------------------------------------------- fills
    def register_fill(self, pnl: float, is_win: bool) -> None:
        st = self.state
        st.current_block.pnl += pnl
        st.current_block.attempts += 1
        if st.current_block.pnl >= BLOCK_TARGET:
            st.current_block.done = True
        st.day_pnl += pnl
        st.week_pnl += pnl
        st.month_pnl += pnl
        st.balance += pnl
        st.equity = st.balance
        if is_win:
            st.wins += 1
            st.consec_losses = 0
            if st.in_recovery and st.day_pnl >= 0:
                st.in_recovery = False
                log_risk("recovery complete - full risk restored", {})
        else:
            st.losses += 1
            st.consec_losses += 1
        self._check_breakers()
        self.save()
        log_risk("fill registered", {"pnl": round(pnl, 2),
                                     "block": st.current_block.key,
                                     "block_pnl": round(st.current_block.pnl, 2),
                                     "day_pnl": round(st.day_pnl, 2)})

    def snapshot(self) -> dict:
        st = self.state
        return {
            "equity": round(st.equity, 2),
            "block": st.current_block.key,
            "block_pnl": round(st.current_block.pnl, 2),
            "block_target": BLOCK_TARGET,
            "block_attempts": st.current_block.attempts,
            "day_pnl": round(st.day_pnl, 2),
            "week_pnl": round(st.week_pnl, 2),
            "month_pnl": round(st.month_pnl, 2),
            "recovery_mode": st.in_recovery,
            "risk_multiplier": st.risk_multiplier,
            "risk_fraction_now": round(self.risk_fraction(), 2),
            "latched": self.is_latched()[0],
            "wins": st.wins,
            "losses": st.losses,
        }
