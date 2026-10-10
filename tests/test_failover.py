"""P1-B :: failover must be explicit; live->paper swaps are forbidden."""
from __future__ import annotations

import pytest

import bot as bot_mod
from brokers.factory import (
    BrokerHaltError,
    connect_with_failover,
    normalize,
    normalize_valid,
)


class StubBroker:
    def __init__(self, name: str, ok: bool):
        self.name = name
        self._ok = ok
        self.status_reason = "" if ok else "stub: connect refused"
        self.connected = False

    def connect(self) -> bool:
        self.connected = self._ok
        return self._ok

    def disconnect(self) -> None:
        self.connected = False


@pytest.fixture()
def stub_factory(monkeypatch):
    def install(ok: bool):
        made: list[str] = []

        def _create(cfg, choice=None):
            kind = normalize_valid(choice or cfg.broker)
            made.append(kind)
            return StubBroker(kind, ok)

        monkeypatch.setattr("brokers.factory.create_broker", _create)
        return made

    return install


class TestHaltSemantics:
    def test_mt5_failure_halts_no_paper_order(self, mk_cfg, stub_factory):
        mk_cfg.broker = "MT5"
        stub_factory(ok=False)
        with pytest.raises(BrokerHaltError) as exc:
            connect_with_failover(mk_cfg, None)
        assert "MT5" in str(exc.value)
        assert "No silent fallback" in str(exc.value)

    def test_mt5_failure_with_allow_flag_still_halts(self, mk_cfg,
                                                     stub_factory,
                                                     monkeypatch):
        """The flag can never authorize a live->paper swap."""
        mk_cfg.broker = "MT5"
        monkeypatch.setenv("ALLOW_PAPER_FALLBACK", "true")
        stub_factory(ok=False)
        with pytest.raises(BrokerHaltError):
            connect_with_failover(mk_cfg, None)

    def test_paper_failure_halts_too(self, mk_cfg, stub_factory):
        mk_cfg.broker = "PAPER"
        stub_factory(ok=False)
        with pytest.raises(BrokerHaltError):
            connect_with_failover(mk_cfg, None)

    def test_unknown_broker_name_refused(self, mk_cfg):
        mk_cfg.broker = "FIDELITY"
        with pytest.raises(BrokerHaltError):
            connect_with_failover(mk_cfg, None)

    def test_success_returns_requested_kind(self, mk_cfg, stub_factory):
        mk_cfg.broker = "MT5"
        made = stub_factory(ok=True)
        broker, attempted = connect_with_failover(mk_cfg, None)
        assert broker.name == "MT5"
        assert attempted == ["MT5"]
        assert made == ["MT5"]


class TestRequestedVsActive:
    def test_normalize_keeps_unknown_explicit(self):
        assert normalize("FIDELITY") == "UNKNOWN"
        with pytest.raises(BrokerHaltError):
            normalize_valid("FIDELITY")

    def test_bot_labels_no_fallback_when_matching(self, mk_bot):
        assert mk_bot.active_broker == mk_bot.requested_broker
        label = mk_bot._broker_label()
        assert "fallback" not in label.lower()
        assert "PAPER" in label

    def test_bot_labels_fallback_explicitly(self, mk_bot):
        mk_bot.requested_broker = "MT5"
        mk_bot.active_broker = "PAPER"
        assert "fallback from MT5" in mk_bot._broker_label()

    def test_main_exits_3_on_halt_and_does_not_restart(
            self, monkeypatch, capsys):
        """bot.main() must surface the halt as exit code 3 and the
        supervisor must NOT restart it (manual .env fix required)."""
        sleeps: list[float] = []
        monkeypatch.setattr(bot_mod.time, "sleep",
                            lambda s: sleeps.append(s))
        # stub BOTH the constructor and start(): main() builds the bot with
        # the real CONFIG, and a real constructor would touch repo data/
        monkeypatch.setattr(bot_mod.ReaperApexBot, "__init__",
                            lambda self, *a, **k: None)
        monkeypatch.setattr(bot_mod.ReaperApexBot, "start",
                            lambda self: 3)
        monkeypatch.setattr("sys.argv", ["bot.py", "--broker", "MT5"])
        monkeypatch.setenv("SUPERVISOR", "1")
        code = bot_mod.main()
        assert code == 3
        assert sleeps == [], "a halted bot must never be auto-restarted"

    def test_main_config_gate_returns_2(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "abc")
        monkeypatch.setattr("sys.argv", ["bot.py"])
        assert bot_mod.main() == 2
