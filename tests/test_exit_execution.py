"""P0-A :: paper trades MUST close on EXIT_SL / EXIT_TP.

Covers both enforcement paths:
  1. bot._manage_positions executes the frozen strategy's exit actions
     (previously ignored - the defect).
  2. PaperBroker's independent safety net closes positions on SL/TP
     crosses even when the strategy loop never acts, with the
     same-candle both-hit rule resolving stop-first (conservative).
"""
from __future__ import annotations

import pytest

from tests.conftest import make_h1

SL = 2390.0
TP = 2430.0
QTY = 0.1


def _open_long(bot, qty: float = QTY):
    """Open a long at 2400 (last_price stub) with SL 2390 / TP 2430."""
    res = bot.broker.market_order("LONG", qty, SL, TP,
                                  "LONDON_NY_OVERLAP", "test")
    assert res.ok
    return res


def _expected_pnl(bot, exit_px: float, qty: float = QTY) -> float:
    from brokers.paper_broker import SLIPPAGE

    entry = bot.broker.closed_trades[-1]["entry"]
    return (exit_px - entry) * qty - SLIPPAGE * qty


class TestBotExecutesStrategyExits:
    """The bot-level switch: EXIT_SL / EXIT_TP trigger real closes."""

    def test_sl_cross_closes_at_stop(self, mk_bot):
        res = _open_long(mk_bot)
        h1 = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0)
        mk_bot._manage_positions(h1)
        assert mk_bot.broker.open_positions() == []
        trade = mk_bot.broker.closed_trades[0]
        assert trade["reason"] == "SL"
        assert trade["exit_price"] == pytest.approx(SL)
        assert trade["ticket"] == res.ticket
        # net of exit fee, filled at the intended stop price
        assert trade["pnl"] == pytest.approx(_expected_pnl(mk_bot, SL))
        assert mk_bot.broker.balance() == pytest.approx(
            mk_bot.cfg.starting_balance + trade["pnl"])

    def test_tp_cross_closes_at_target(self, mk_bot):
        _open_long(mk_bot)
        h1 = make_h1(last_high=2450.0, last_low=2431.0, last_close=2440.0)
        mk_bot._manage_positions(h1)
        assert mk_bot.broker.open_positions() == []
        trade = mk_bot.broker.closed_trades[0]
        assert trade["reason"] == "TP"
        assert trade["exit_price"] == pytest.approx(TP)
        assert trade["pnl"] == pytest.approx(_expected_pnl(mk_bot, TP))

    def test_same_candle_both_hit_closes_sl_first(self, mk_bot):
        """Bar 2385-2440 crosses SL and TP: conservative = stop first."""
        _open_long(mk_bot)
        h1 = make_h1(last_high=2440.0, last_low=2385.0, last_close=2400.0)
        mk_bot._manage_positions(h1)
        trade = mk_bot.broker.closed_trades[0]
        assert trade["reason"] == "SL"
        assert trade["exit_price"] == pytest.approx(SL)

    def test_sl_only_touch_closes(self, mk_bot):
        """Low == SL exactly is a touch -> close."""
        _open_long(mk_bot)
        h1 = make_h1(last_high=2401.0, last_low=SL, last_close=2395.0)
        mk_bot._manage_positions(h1)
        assert mk_bot.broker.open_positions() == []
        assert mk_bot.broker.closed_trades[0]["reason"] == "SL"

    def test_double_manage_closes_exactly_once(self, mk_bot):
        _open_long(mk_bot)
        h1 = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0)
        mk_bot._manage_positions(h1)
        mk_bot._manage_positions(h1)   # second pass: nothing left to manage
        assert len(mk_bot.broker.closed_trades) == 1
        assert mk_bot.broker.open_positions() == []

    def test_duplicate_close_position_is_deduped(self, mk_bot):
        res = _open_long(mk_bot)
        pnl1 = mk_bot.broker.close_position(res.ticket, reason="SL",
                                            intended_price=SL)
        assert pnl1 is not None
        pnl2 = mk_bot.broker.close_position(res.ticket, reason="SL",
                                            intended_price=SL)
        assert pnl2 is None, "second close must be refused (exactly-once)"
        assert len(mk_bot.broker.closed_trades) == 1

    def test_close_records_full_detail(self, mk_bot):
        _open_long(mk_bot)
        h1 = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0)
        mk_bot._manage_positions(h1)
        trade = mk_bot.broker.closed_trades[0]
        for key in ("ticket", "side", "size_oz", "entry", "exit_price",
                    "reason", "exit_ts", "pnl", "fee"):
            assert key in trade, f"close record missing {key}"
        assert trade["exit_ts"]  # timestamped
        assert trade["fee"] >= 0


class TestIndependentSafetyNet:
    """PaperBroker enforces stops even when the strategy loop is silent."""

    def test_enforce_stops_closes_without_strategy_loop(self, paper_broker):
        res = paper_broker.market_order("LONG", QTY, SL, TP, "S", "t")
        assert paper_broker.open_positions(), "position open before net"
        bars = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0,
                       bars=2)
        closes = paper_broker.enforce_stops(bars)
        assert closes == 1
        assert paper_broker.open_positions() == []
        assert paper_broker.closed_trades[0]["ticket"] == res.ticket
        assert paper_broker.closed_trades[0]["reason"] == "SL"

    def test_both_hit_conservative_reason_and_price(self, paper_broker):
        paper_broker.market_order("LONG", QTY, SL, TP, "S", "t")
        bars = make_h1(last_high=2440.0, last_low=2385.0, last_close=2400.0,
                       bars=2)
        paper_broker.enforce_stops(bars)
        trade = paper_broker.closed_trades[0]
        assert trade["reason"] == "EXIT_SL_CONSERVATIVE"
        assert trade["exit_price"] == pytest.approx(SL)

    def test_candles_path_triggers_safety_net(self, paper_broker, monkeypatch):
        """Even the ordinary candle refresh enforces stops (no strategy
        loop involved at all)."""
        paper_broker.market_order("SHORT", QTY, 2410.0, 2370.0, "S", "t")
        idx = make_h1(last_high=2411.0, last_low=2400.0, last_close=2405.0,
                      bars=2).index
        import pandas as pd

        feed = pd.DataFrame({
            "open": [2400.0, 2400.0], "high": [2400.5, 2411.0],
            "low": [2399.5, 2400.0], "close": [2400.0, 2405.0],
            "volume": [1.0, 1.0]}, index=idx)
        monkeypatch.setattr(paper_broker, "_yahoo",
                            lambda period, interval: feed)
        paper_broker.candles("h1", 5)   # short's SL 2410 <= high 2411 -> out
        assert paper_broker.open_positions() == []
        trade = paper_broker.closed_trades[0]
        assert trade["reason"] == "SL"
        assert trade["exit_price"] == pytest.approx(2410.0)

    def test_no_close_on_bars_predating_entry(self, paper_broker):
        import pandas as pd

        past = pd.Timestamp.now("UTC").floor("1h") - pd.Timedelta(hours=48)
        bars = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0,
                       bars=2, end=past)
        # open AFTER the crossing bar exists: net must not replay history
        paper_broker.market_order("LONG", QTY, SL, TP, "S", "t")
        closes = paper_broker.enforce_stops(bars)
        assert closes == 0
        assert len(paper_broker.open_positions()) == 1

    def test_short_mirror_sl_and_tp(self, paper_broker):
        paper_broker.market_order("SHORT", QTY, 2410.0, 2370.0, "S", "t")
        bars = make_h1(last_high=2431.0, last_low=2400.0, last_close=2420.0,
                       bars=2)
        paper_broker.enforce_stops(bars)
        trade = paper_broker.closed_trades[0]
        assert trade["reason"] == "SL"
        assert trade["exit_price"] == pytest.approx(2410.0)

    def test_short_tp_hit(self, paper_broker):
        paper_broker.market_order("SHORT", QTY, 2410.0, 2370.0, "S", "t")
        bars = make_h1(last_high=2400.0, last_low=2360.0, last_close=2370.0,
                       bars=2)
        paper_broker.enforce_stops(bars)
        trade = paper_broker.closed_trades[0]
        assert trade["reason"] == "TP"
        assert trade["exit_price"] == pytest.approx(2370.0)

    def test_net_is_idempotent_across_calls(self, paper_broker):
        paper_broker.market_order("LONG", QTY, SL, TP, "S", "t")
        bars = make_h1(last_high=2410.0, last_low=2389.0, last_close=2395.0,
                       bars=2)
        assert paper_broker.enforce_stops(bars) == 1
        assert paper_broker.enforce_stops(bars) == 0, \
            "replayed bars must not close again"
        assert len(paper_broker.closed_trades) == 1
