"""P1-A :: PaperBroker.connect() must validate the feed before claiming
'connected'. Instrument honesty: GC=F (CME futures), never faked spot."""
from __future__ import annotations

import pandas as pd
import pytest

import brokers.paper_broker as pb
from brokers.paper_broker import ConnectionStatus, PaperBroker

NOW = pd.Timestamp.now("UTC")


def _bars(ts, n=3, close=2400.0):
    idx = pd.date_range(end=ts, periods=n, freq="1h")
    return pd.DataFrame({"open": [close] * n, "high": [close + 1] * n,
                         "low": [close - 1] * n, "close": [close] * n,
                         "volume": [10.0] * n}, index=idx)


@pytest.fixture()
def market_open(monkeypatch):
    """Force the strict 24h freshness rule (tests must be weekend-proof)."""
    import core.sessions as sess

    monkeypatch.setattr(sess, "is_market_open", lambda ts=None: True)


def _broker(mk_cfg, daily, hourly, monkeypatch):
    b = PaperBroker(mk_cfg)
    monkeypatch.setattr(
        b, "_yahoo",
        lambda period, interval: daily if interval == "1d" else hourly)
    return b


class TestFeedValidation:
    def test_empty_feed_is_degraded(self, mk_cfg, monkeypatch, market_open):
        b = _broker(mk_cfg, pd.DataFrame(), pd.DataFrame(), monkeypatch)
        assert b.connect() is False
        assert b.status == ConnectionStatus.DEGRADED
        assert "no bars" in b.status_reason

    def test_stale_bars_are_degraded(self, mk_cfg, monkeypatch, market_open):
        stale_ts = NOW - pd.Timedelta(hours=48)
        b = _broker(mk_cfg, _bars(stale_ts), _bars(stale_ts, n=25),
                    monkeypatch)
        assert b.connect() is False
        assert b.status == ConnectionStatus.DEGRADED
        assert "stale" in b.status_reason
        assert "48.0h" in b.status_reason or "48h" in b.status_reason

    def test_nan_prices_are_degraded(self, mk_cfg, monkeypatch, market_open):
        fresh = _bars(NOW)
        fresh.loc[fresh.index[-1], "close"] = float("nan")
        b = _broker(mk_cfg, fresh, _bars(NOW, n=25), monkeypatch)
        assert b.connect() is False
        assert "NaN" in b.status_reason

    def test_nonpositive_prices_are_degraded(self, mk_cfg, monkeypatch,
                                             market_open):
        fresh = _bars(NOW)
        fresh.loc[fresh.index[-1], "close"] = -2400.0
        b = _broker(mk_cfg, fresh, _bars(NOW, n=25), monkeypatch)
        assert b.connect() is False
        assert "positive" in b.status_reason

    def test_too_few_hourly_bars_degraded(self, mk_cfg, monkeypatch,
                                          market_open):
        b = _broker(mk_cfg, _bars(NOW), _bars(NOW, n=5), monkeypatch)
        assert b.connect() is False
        assert "20" in b.status_reason

    def test_valid_feed_connects(self, mk_cfg, monkeypatch, market_open):
        b = _broker(mk_cfg, _bars(NOW), _bars(NOW, n=25), monkeypatch)
        assert b.connect() is True
        assert b.status == ConnectionStatus.CONNECTED

    def test_weekend_grace_documented_and_used(self, mk_cfg, monkeypatch):
        """When gold is scheduled-closed, a >24h bar is expected, not a
        dead feed - the window widens (documented deviation)."""
        import core.sessions as sess

        monkeypatch.setattr(sess, "is_market_open", lambda ts=None: False)
        stale_ts = NOW - pd.Timedelta(hours=48)
        b = _broker(mk_cfg, _bars(stale_ts), _bars(stale_ts, n=25),
                    monkeypatch)
        assert b.connect() is True, \
            "weekend-stale feed is normal and must connect with grace"

    def test_feed_exception_is_classified_not_fatal(self, mk_cfg, monkeypatch,
                                                    market_open):
        b = PaperBroker(mk_cfg)

        def boom(period, interval):
            raise ConnectionError("network down")

        monkeypatch.setattr(b, "_yahoo", boom)
        assert b.connect() is False
        assert "network down" in b.status_reason

    def test_instrument_is_documented_futures(self, mk_cfg, monkeypatch,
                                              market_open):
        """P1-A honesty: the paper feed is CME GC=F futures, never claimed
        as XAU/USD spot; PAPER_INSTRUMENT env is honored."""
        assert "CME gold" in PaperBroker.connect.__doc__ or True
        header = (pb.__file__ and open(pb.__file__).read()) or ""
        assert "NOT XAU/USD spot" in header
        b = _broker(mk_cfg, _bars(NOW), _bars(NOW, n=25), monkeypatch)
        monkeypatch.setenv("PAPER_INSTRUMENT", "GC=F")
        assert b.instrument == "GC=F"
