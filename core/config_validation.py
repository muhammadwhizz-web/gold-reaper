"""
GOLD REAPER :: Config Validation (strict, refuse-to-start)
==========================================================
REPAIR CONTRACT (branch fix/reliability-v1, P1-C):

core/config.py is FROZEN (docs/PROTECTED_HASHES.txt) and its _f()/_i()
helpers silently fall back to defaults on garbage values. This module
re-reads the RAW environment strings and validates every number the bot
lives and dies by. Invalid config = ConfigError naming the key and the
bad value = the bot refuses to start. No silent fallbacks.

Also hosts resolve_dashboard_bind() (P1-D): dashboards bind 127.0.0.1
unless explicitly opted out with an authenticated configuration.
"""
from __future__ import annotations

import os

from brokers.factory import BrokerHaltError, normalize_valid

RISK_MAX_PCT = 3.0
DAILY_LOSS_MIN_PCT = -10.0


class ConfigError(RuntimeError):
    """Invalid configuration - the bot must not start."""


def _require_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError as e:
        raise ConfigError(
            f"{key}={raw!r} is not a number. Fix .env (or remove the key "
            f"to use the default {default}).") from e


def _require_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as e:
        raise ConfigError(
            f"{key}={raw!r} is not an integer. Fix .env (or remove the "
            f"key to use the default {default}).") from e


def validate_config() -> dict[str, float | int | str]:
    """Validate every tunable that influences money. Returns the parsed
    values so callers can log what actually runs. Raises ConfigError."""
    values: dict[str, float | int | str] = {}

    # ── broker identity (strict: no silent PAPER coercion) ────────
    raw_broker = os.getenv("BROKER", "PAPER")
    try:
        values["BROKER"] = normalize_valid(raw_broker)
    except BrokerHaltError as e:
        raise ConfigError(str(e)) from e

    # ── PAPER_MODE live gate (SD-13 mitigation) ───────────────────
    # cfg.paper exists but was never read by the factory: BROKER=MT5 with
    # the .env-template default PAPER_MODE=true used to connect a LIVE
    # account while the user believed paper mode was active. Live trading
    # now requires PAPER_MODE to be explicitly false.
    if values["BROKER"] in ("MT5", "BITGET"):
        raw_pm = os.getenv("PAPER_MODE", "").strip().lower()
        if raw_pm not in ("false", "0", "no", "off"):
            raise ConfigError(
                f"BROKER={values['BROKER']} is a LIVE venue but "
                f"PAPER_MODE={os.getenv('PAPER_MODE', '(unset)')!r}. "
                f"Live trading requires PAPER_MODE=false set explicitly "
                f"in .env (this gate exists so a leftover template value "
                f"can never put real money at risk by accident).")

    # ── session windows / entry hours (CFG: empty tuple = 24h trading) ─
    raw_hours = os.getenv("ENTRY_HOURS_UTC", "")
    hours_strip = raw_hours.strip()
    if hours_strip:
        tokens = [t.strip() for t in hours_strip.split(",")]
        valid = [t for t in tokens if t.isdigit() and 0 <= int(t) <= 23]
        if not valid:
            raise ConfigError(
                f"ENTRY_HOURS_UTC={raw_hours!r} contains no valid UTC hour "
                f"(0-23). An empty entry-hours list silently disables the "
                f"hour filter and trades around the clock - fix .env.")
    values["ENTRY_HOURS_UTC"] = hours_strip or "(default)"

    # ── live credentials sanity (only when a live venue is requested) ──
    if values["BROKER"] == "MT5":
        raw_login = os.getenv("MT5_LOGIN", "12345678").strip()
        if not raw_login.isdigit() or int(raw_login) <= 0:
            raise ConfigError(
                f"MT5_LOGIN={raw_login!r} is not a valid account id. An "
                f"invalid login makes the adapter skip login() and trade "
                f"on whatever account the terminal already holds.")
    if values["BROKER"] == "BITGET":
        lev = _require_float("BITGET_LEVERAGE", 3.0)
        if not (0 < lev <= 125):
            raise ConfigError(
                f"BITGET_LEVERAGE={lev} must be within (0, 125].")
        values["BITGET_LEVERAGE"] = lev

    # ── capital & risk ─────────────────────────────────────────────
    balance = _require_float("STARTING_BALANCE", 1000.0)
    if balance <= 0:
        raise ConfigError(f"STARTING_BALANCE={balance} must be > 0")
    values["STARTING_BALANCE"] = balance

    risk = _require_float("RISK_PER_TRADE_PCT", 1.0)
    if risk <= 0:
        raise ConfigError(f"RISK_PER_TRADE_PCT={risk} must be > 0")
    if risk > RISK_MAX_PCT:
        raise ConfigError(
            f"RISK_PER_TRADE_PCT={risk}% exceeds the hard ceiling of "
            f"{RISK_MAX_PCT}%. If you truly want this, edit "
            f"core/config_validation.py and accept the consequences in "
            f"writing.")
    values["RISK_PER_TRADE_PCT"] = risk

    daily = _require_float("MAX_DAILY_LOSS_PCT", 3.0)
    # repo convention: stored POSITIVE (3.0 = "stop after losing 3%").
    # Accept either sign but enforce the magnitude band (0, 10] and
    # reject an explicit 0 (disabling the breaker must be deliberate).
    if daily == 0 or not (0 < abs(daily) <= abs(DAILY_LOSS_MIN_PCT)):
        raise ConfigError(
            f"MAX_DAILY_LOSS_PCT={daily} must be within (0, "
            f"{abs(DAILY_LOSS_MIN_PCT):.0f}] percent "
            f"(repo convention: positive = loss bound)")
    values["MAX_DAILY_LOSS_PCT"] = abs(daily)

    consec = _require_int("MAX_CONSEC_LOSSES", 3)
    if consec < 1:
        raise ConfigError(f"MAX_CONSEC_LOSSES={consec} must be >= 1")
    values["MAX_CONSEC_LOSSES"] = consec

    max_open = _require_int("MAX_OPEN_TRADES", 1)
    if max_open < 1:
        raise ConfigError(f"MAX_OPEN_TRADES={max_open} must be >= 1")
    values["MAX_OPEN_TRADES"] = max_open

    per_session = _require_int("MAX_TRADES_PER_SESSION", 4)
    if per_session < 1:
        raise ConfigError(
            f"MAX_TRADES_PER_SESSION={per_session} must be >= 1")
    values["MAX_TRADES_PER_SESSION"] = per_session

    # ── hunt targets ───────────────────────────────────────────────
    target_session = _require_float("TARGET_PER_SESSION_USD", 20.0)
    if target_session <= 0:
        raise ConfigError(
            f"TARGET_PER_SESSION_USD={target_session} must be > 0")
    values["TARGET_PER_SESSION_USD"] = target_session

    target_day = _require_float("TARGET_PER_DAY_USD", 120.0)
    if target_day <= 0:
        raise ConfigError(f"TARGET_PER_DAY_USD={target_day} must be > 0")
    values["TARGET_PER_DAY_USD"] = target_day

    # ── strategy geometry (units sanity, not strategy changes) ────
    sl_mult = _require_float("SL_ATR_MULT", 1.2)
    if sl_mult <= 0:
        raise ConfigError(f"SL_ATR_MULT={sl_mult} must be > 0")
    values["SL_ATR_MULT"] = sl_mult

    tp_r = _require_float("TP_R", 2.0)
    if tp_r <= 0:
        raise ConfigError(f"TP_R={tp_r} must be > 0")
    values["TP_R"] = tp_r

    poll = _require_int("POLL_SECONDS", 60)
    if poll < 5:
        raise ConfigError(
            f"POLL_SECONDS={poll} must be >= 5 (the feed is hourly; "
            f"hammering it gets you rate-limited)")
    values["POLL_SECONDS"] = poll

    return values


# ─────────────────────────────────────────────── P1-D dashboard binding

def resolve_dashboard_bind(port: int = 8080) -> tuple[str, int]:
    """Dashboard bind policy: localhost by default.

    DASHBOARD_BIND=0.0.0.0 opts into remote binding and REQUIRES
    DASHBOARD_USER + DASHBOARD_PASS (HTTP basic auth) - refuse to start
    otherwise. Returns (host, port)."""
    host = os.getenv("DASHBOARD_BIND", "127.0.0.1").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost") :
        user = os.getenv("DASHBOARD_USER", "").strip()
        pwd = os.getenv("DASHBOARD_PASS", "").strip()
        if not (user and pwd):
            raise ConfigError(
                f"DASHBOARD_BIND={host} exposes the console (kill-switch "
                f"endpoint included) beyond localhost. Set DASHBOARD_USER "
                f"and DASHBOARD_PASS in .env, or use 127.0.0.1.")
    return host, port
