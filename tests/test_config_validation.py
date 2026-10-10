"""P1-C :: strict config validation - bad values refuse to start."""
from __future__ import annotations

import pytest

from core.config_validation import (
    ConfigError,
    resolve_dashboard_bind,
    validate_config,
)


class TestRejects:
    def test_non_numeric_risk_rejected(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "abc")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "RISK_PER_TRADE_PCT" in str(exc.value)
        assert "abc" in str(exc.value)

    def test_risk_above_ceiling_rejected(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "10")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "RISK_PER_TRADE_PCT" in str(exc.value)

    def test_risk_zero_rejected(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0")
        with pytest.raises(ConfigError):
            validate_config()

    def test_daily_loss_out_of_band_rejected(self, monkeypatch):
        for bad in ("0", "15", "-15"):
            monkeypatch.setenv("MAX_DAILY_LOSS_PCT", bad)
            with pytest.raises(ConfigError) as exc:
                validate_config()
            assert "MAX_DAILY_LOSS_PCT" in str(exc.value)

    def test_session_target_nonpositive_rejected(self, monkeypatch):
        monkeypatch.setenv("TARGET_PER_SESSION_USD", "0")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "TARGET_PER_SESSION_USD" in str(exc.value)

    def test_day_target_nonpositive_rejected(self, monkeypatch):
        monkeypatch.setenv("TARGET_PER_DAY_USD", "-5")
        with pytest.raises(ConfigError):
            validate_config()

    def test_invalid_broker_rejected(self, monkeypatch):
        monkeypatch.setenv("BROKER", "FIDELITY")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "FIDELITY" in str(exc.value)

    def test_non_numeric_balance_rejected(self, monkeypatch):
        monkeypatch.setenv("STARTING_BALANCE", "lots")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "STARTING_BALANCE" in str(exc.value)

    def test_poll_too_fast_rejected(self, monkeypatch):
        monkeypatch.setenv("POLL_SECONDS", "1")
        with pytest.raises(ConfigError):
            validate_config()


class TestAccepts:
    def test_valid_config_loads(self, monkeypatch):
        monkeypatch.delenv("RISK_PER_TRADE_PCT", raising=False)
        monkeypatch.delenv("BROKER", raising=False)
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        values = validate_config()
        assert values["BROKER"] in ("MT5", "BITGET", "PAPER")
        assert 0 < values["RISK_PER_TRADE_PCT"] <= 3.0

    def test_broker_aliases_accepted(self, monkeypatch):
        monkeypatch.setenv("BROKER", "EXNESS")
        # live venue: the PAPER_MODE gate (SD-13 mitigation) demands an
        # explicit opt-out before a live adapter may connect
        monkeypatch.setenv("PAPER_MODE", "false")
        assert validate_config()["BROKER"] == "MT5"

    def test_daily_loss_negative_entry_normalized(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "-5")
        assert validate_config()["MAX_DAILY_LOSS_PCT"] == 5.0

    def test_error_names_key_and_value(self, monkeypatch):
        monkeypatch.setenv("TARGET_PER_SESSION_USD", "abc")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        msg = str(exc.value)
        assert "TARGET_PER_SESSION_USD" in msg and "abc" in msg


class TestPaperModeLiveGate:
    """BROKER=MT5/BITGET with a leftover template PAPER_MODE=true used to
    connect a LIVE account while the user believed paper mode was active."""

    def test_live_broker_paper_mode_true_rejected(self, monkeypatch):
        monkeypatch.setenv("BROKER", "MT5")
        monkeypatch.setenv("PAPER_MODE", "true")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "PAPER_MODE" in str(exc.value)

    def test_live_broker_paper_mode_unset_rejected(self, monkeypatch):
        monkeypatch.setenv("BROKER", "MT5")
        monkeypatch.delenv("PAPER_MODE", raising=False)
        with pytest.raises(ConfigError):
            validate_config()

    def test_live_broker_paper_mode_false_accepted(self, monkeypatch):
        monkeypatch.setenv("BROKER", "MT5")
        monkeypatch.setenv("PAPER_MODE", "false")
        monkeypatch.delenv("MT5_LOGIN", raising=False)
        assert validate_config()["BROKER"] == "MT5"

    def test_entry_hours_garbage_rejected(self, monkeypatch):
        monkeypatch.delenv("BROKER", raising=False)
        monkeypatch.setenv("ENTRY_HOURS_UTC", "12-13")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "ENTRY_HOURS_UTC" in str(exc.value)

    def test_entry_hours_valid_accepted(self, monkeypatch):
        monkeypatch.delenv("BROKER", raising=False)
        monkeypatch.setenv("ENTRY_HOURS_UTC", "12,13")
        validate_config()  # must not raise

    def test_mt5_login_garbage_rejected(self, monkeypatch):
        monkeypatch.setenv("BROKER", "MT5")
        monkeypatch.setenv("PAPER_MODE", "false")
        monkeypatch.setenv("MT5_LOGIN", "abc")
        with pytest.raises(ConfigError) as exc:
            validate_config()
        assert "MT5_LOGIN" in str(exc.value)


class TestDashboardBind:
    def test_default_is_localhost(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_BIND", raising=False)
        assert resolve_dashboard_bind(8080) == ("127.0.0.1", 8080)

    def test_remote_bind_requires_auth(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_BIND", "0.0.0.0")
        monkeypatch.delenv("DASHBOARD_USER", raising=False)
        monkeypatch.delenv("DASHBOARD_PASS", raising=False)
        with pytest.raises(ConfigError) as exc:
            resolve_dashboard_bind(8080)
        assert "DASHBOARD_USER" in str(exc.value)

    def test_remote_bind_with_auth_ok(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_BIND", "0.0.0.0")
        monkeypatch.setenv("DASHBOARD_USER", "reaper")
        monkeypatch.setenv("DASHBOARD_PASS", "honest-losses")
        assert resolve_dashboard_bind(8080) == ("0.0.0.0", 8080)
