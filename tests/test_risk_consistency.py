"""P0-C :: ONE canonical account truth -> both risk layers agree."""
from __future__ import annotations

import pytest


def _feed_stream(bot, stream):
    """Apply the exact wiring bot.py uses per tick:
    hydrate (tick start, from equity sync) -> fill registration -> save."""
    for pnl, is_win in stream:
        bot._hydrate_risk_from_account()
        bot.account.register_fill(pnl, is_win)
        bot.account.save()
        bot.risk.register_fill("LONDON_NY_OVERLAP", pnl, is_win)
        bot.apex.register_fill(pnl, is_win)


class TestSingleSourceOfTruth:
    def test_both_layers_see_identical_inputs(self, mk_bot):
        _feed_stream(mk_bot, [(-20.0, False), (35.0, True), (-22.0, False)])
        acc = mk_bot.account
        assert mk_bot.risk.state.balance == pytest.approx(acc.balance)
        assert mk_bot.risk.state.equity == pytest.approx(acc.equity)
        assert mk_bot.apex.state.balance == pytest.approx(acc.balance)
        assert mk_bot.apex.state.equity == pytest.approx(acc.equity)
        assert mk_bot.apex.state.day_pnl == pytest.approx(acc.day_pnl)

    def test_identical_stream_identical_decisions(self, mk_bot):
        # 3 consecutive losses: -6% day (breaches both -3% gates) AND
        # hits the consecutive-loss cap
        _feed_stream(mk_bot, [(-20.0, False)] * 3)
        ok_r, why_r = mk_bot.risk.can_trade("LONDON_NY_OVERLAP")
        ok_a, why_a = mk_bot.apex.can_open(0.9)
        assert ok_r is False and ok_a is False, \
            f"both must veto (risk={why_r!r} apex={why_a!r})"

    def test_breaker_latches_exactly_once_per_day(self, mk_bot, monkeypatch):
        """Gate-path contract: the breaker latches (and alerts) ONCE;
        repeated gates stay vetoed silently. (ApexRisk.register_fill
        re-latching on every breaching fill is SD-6 - frozen file.)"""
        import core.notify as notify_mod

        calls: list[tuple] = []
        monkeypatch.setattr(notify_mod, "notify",
                            lambda t, m: calls.append((t, m)))
        mk_bot.apex.sync_period_starts()          # settle day anchors
        mk_bot.apex.state.day_start_equity = 1000.0
        mk_bot.apex.state.day_pnl = -80.0          # -8% day

        first = mk_bot.apex.can_open(0.9)
        second = mk_bot.apex.can_open(0.9)
        third = mk_bot.apex.can_open(0.9)
        assert first[0] is False and second[0] is False and third[0] is False
        assert len(calls) == 1, "breaker must latch (and alert) exactly once"
        latched, _ = mk_bot.apex.is_latched()
        assert latched

    def test_restart_midday_enforces_identically(self, mk_cfg, mk_bot):
        _feed_stream(mk_bot, [(-20.0, False)])   # one loss, limits not hit
        snapshot = (mk_bot.account.balance, mk_bot.account.day_pnl,
                    mk_bot.account.consec_losses)

        from bot import ReaperApexBot

        bot2 = ReaperApexBot(mk_cfg)   # "restart": same hermetic paths
        bot2._hydrate_risk_from_account()
        assert (bot2.account.balance, bot2.account.day_pnl,
                bot2.account.consec_losses) == snapshot, \
            "restart must reload persisted state, no discrepancy allowed"
        ok_r, _ = bot2.risk.can_trade("LONDON_NY_OVERLAP")
        ok_a, _ = bot2.apex.can_open(0.9)
        assert ok_r is True and ok_a is True   # same decisions pre/post

    def test_legacy_risk_streak_gate_works_again(self, mk_bot):
        """Regression: RiskManager's day counters used to stay at zero
        forever because bot.py only registered fills into ApexRisk."""
        assert mk_bot.risk.state.day.consec_losses == 0
        _feed_stream(mk_bot, [(-20.0, False), (-20.0, False)])
        assert mk_bot.risk.state.day.consec_losses == 2, \
            "legacy manager must see every fill (P0-C wiring)"
        assert mk_bot.risk.state.day.realized_pnl == pytest.approx(-40.0)

    def test_account_state_persists_across_restart(self, mk_cfg, mk_bot):
        _feed_stream(mk_bot, [(30.0, True)])
        from core.account_state import AccountState

        reloaded = AccountState.load(mk_cfg.starting_balance)
        assert reloaded.balance == pytest.approx(
            mk_cfg.starting_balance + 30.0)
        assert reloaded.wins == 1
        assert reloaded.day_pnl == pytest.approx(30.0)

    def test_corrupt_account_state_refuses(self, mk_cfg, gr_paths):
        gr_paths.joinpath("account_state.json").write_text("{corrupt")
        from core.account_state import AccountState, AccountStateError

        with pytest.raises(AccountStateError):
            AccountState.load(mk_cfg.starting_balance)

    def test_equity_sync_does_not_corrupt_realized_truth(self, mk_bot):
        _feed_stream(mk_bot, [(-20.0, False)])
        realized = mk_bot.account.realized_pnl
        mk_bot.account.sync_equity(12345.0)   # open-position MTM noise
        assert mk_bot.account.realized_pnl == pytest.approx(realized)
        assert mk_bot.account.balance == pytest.approx(
            mk_cfg_start(mk_bot) - 20.0)


def mk_cfg_start(bot) -> float:
    return bot.cfg.starting_balance
