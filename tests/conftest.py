"""Shared fixtures: hermetic paths + bot factory.

Every state file the repair touches is redirected into tmp_path so tests
can never pollute (or read) the repo's real data/ runtime state."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def gr_paths(tmp_path, monkeypatch):
    """Redirect every runtime state file into tmp."""
    import bot as bot_mod
    import brokers.paper_broker as pb
    import core.account_state as acc
    import core.audit as audit_mod
    import core.risk_apex as ra

    monkeypatch.setattr(pb, "ACCOUNT_FILE", tmp_path / "paper_account.json")
    monkeypatch.setattr(pb, "DUCKDB_FILE", tmp_path / "paper.duckdb")
    monkeypatch.setattr(acc, "ACCOUNT_STATE_FILE",
                        tmp_path / "account_state.json")
    monkeypatch.setattr(ra, "RISK_FILE", tmp_path / "apex_risk.json")
    monkeypatch.setattr(ra, "BREAKER_FILE", tmp_path / "breakers.json")
    monkeypatch.setattr(audit_mod, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(bot_mod, "HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(bot_mod, "PID_FILE", tmp_path / "bot.pid")
    monkeypatch.setattr(bot_mod, "STANDBY_FILE", tmp_path / "standby.flag")
    return tmp_path


@pytest.fixture()
def mk_cfg(gr_paths):
    """A real Config with tmp runtime paths (strategy numbers untouched)."""
    from core.config import Config

    cfg = Config()
    cfg.state_file = gr_paths / "state.json"
    cfg.trades_file = gr_paths / "trades.csv"
    cfg.log_file = gr_paths / "reaper.log"
    return cfg


@pytest.fixture()
def mk_bot(mk_cfg, monkeypatch):
    """A real ReaperApexBot on hermetic paths (no broker connection,
    deterministic paper price - tests never touch the network)."""
    from bot import ReaperApexBot

    bot = ReaperApexBot(mk_cfg)
    monkeypatch.setattr(bot.broker, "last_price", lambda: 2400.0)
    return bot


@pytest.fixture()
def paper_broker(mk_cfg, monkeypatch):
    """A PaperBroker with a deterministic last_price (no network)."""
    from brokers.paper_broker import PaperBroker

    b = PaperBroker(mk_cfg)
    monkeypatch.setattr(b, "last_price", lambda: 2400.0)
    return b


def make_h1(last_high: float, last_low: float, last_close: float,
            bars: int = 30, end: "str | None" = None) -> "object":  # noqa: UP037
    """OHLC frame: flat history + one decisive last bar (UTC index).
    Bars END at `end` (default: the current hour) so the safety net's
    opened_at guard sees them as fresh."""
    import pandas as pd

    if end is None:
        end = pd.Timestamp.now("UTC").floor("1h")
    idx = pd.date_range(end=end, periods=bars, freq="1h")
    base = 2400.0
    df = pd.DataFrame({
        "open": [base] * bars,
        "high": [base + 0.5] * bars,
        "low": [base - 0.5] * bars,
        "close": [base] * bars,
        "volume": [1000.0] * bars,
    }, index=idx)
    df.iloc[-1, df.columns.get_loc("high")] = last_high
    df.iloc[-1, df.columns.get_loc("low")] = last_low
    df.iloc[-1, df.columns.get_loc("close")] = last_close
    return df
