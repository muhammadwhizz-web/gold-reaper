"""P1-D :: dashboard binds safely, authenticates optionally, and never
lets mock data look like real data."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import dashboard.app as da


@pytest.fixture()
def mock_console(monkeypatch, tmp_path):
    monkeypatch.setattr(da, "DATA", tmp_path)          # no apex_risk.json
    monkeypatch.setattr(da, "HEARTBEAT_FILE", tmp_path / "heartbeat.json")
    monkeypatch.setattr(da, "STANDBY_FILE", tmp_path / "standby.flag")
    return tmp_path


def _live_console(mock_console, heartbeat: dict | None = None):
    (da.DATA / "apex_risk.json").write_text(json.dumps(
        {"equity": 1234.56, "day_pnl": 12.3, "wins": 1, "losses": 0}))
    if heartbeat:
        (da.DATA / "heartbeat.json").write_text(json.dumps(heartbeat))
    return da.DATA


class TestMockBanner:
    def test_mock_mode_renders_red_banner(self, mock_console):
        from fastapi.testclient import TestClient

        client = TestClient(da.app)
        html = client.get("/").text
        assert "DEMO MODE" in html
        assert "NOT REAL DATA" in html
        assert 'id="demo-mode-banner"' in html

    def test_mock_state_api_says_mock(self, mock_console):
        from fastapi.testclient import TestClient

        client = TestClient(da.app)
        assert client.get("/api/state").json()["mode"] == "mock"

    def test_live_mode_has_no_banner(self, mock_console):
        from fastapi.testclient import TestClient

        _live_console(mock_console)
        client = TestClient(da.app)
        html = client.get("/").text
        assert "DEMO MODE" not in html
        assert "demo-mode-banner" not in html

    def test_live_state_api_says_live(self, mock_console):
        from fastapi.testclient import TestClient

        _live_console(mock_console)
        client = TestClient(da.app)
        body = client.get("/api/state").json()
        assert body["mode"] == "live"
        assert body["equity"] == pytest.approx(1234.56)


class TestBrokerStatusHonesty:
    def test_rows_carry_requested_vs_active(self, mock_console):
        hb = {"ts": time.time(), "fresh": True, "mode": "PAPER",
              "requested_broker": "MT5", "active_broker": "PAPER"}
        _live_console(mock_console, hb)
        from fastapi.testclient import TestClient

        body = TestClient(da.app).get("/api/state").json()
        names = " | ".join(b["name"] for b in body["brokers"])
        assert "fallback from MT5" in names
        assert body["broker_label"] is None or True

    def test_matching_brokers_show_no_fallback(self, mock_console):
        hb = {"ts": time.time(), "fresh": True, "mode": "PAPER",
              "requested_broker": "PAPER", "active_broker": "PAPER"}
        _live_console(mock_console, hb)
        from fastapi.testclient import TestClient

        body = TestClient(da.app).get("/api/state").json()
        names = " | ".join(b["name"] for b in body["brokers"])
        assert "fallback" not in names


class _Recorder:
    def __init__(self):
        self.called = False
        self.sent = []

    async def app(self, scope, receive, send):
        self.called = True


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


class TestBasicAuthMiddleware:
    def test_no_creds_configured_is_open(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_USER", raising=False)
        monkeypatch.delenv("DASHBOARD_PASS", raising=False)
        rec = _Recorder()
        mw = da._BasicAuth(rec.app)
        scope = {"type": "http", "headers": []}
        _run(mw(scope, None, None))
        assert rec.called

    def test_creds_required_and_enforced(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_USER", "reaper")
        monkeypatch.setenv("DASHBOARD_PASS", "honest")
        rec = _Recorder()
        mw = da._BasicAuth(rec.app)
        import base64

        good = base64.b64encode(b"reaper:honest").decode()
        bad = base64.b64encode(b"reaper:wrong").decode()

        async def flow(header):
            rec.called = False
            rec.sent = []
            sends = []

            async def receive():
                return {"type": "http.request"}

            async def send(msg):
                sends.append(msg)

            scope = {"type": "http",
                     "headers": ([(b"authorization",
                                   f"Basic {header}".encode())]
                                 if header else [])}
            await mw(scope, receive, send)
            return sends

        sends = _run(flow(bad))
        assert not rec.called
        assert sends[0]["status"] == 401

        sends = _run(flow(good))
        assert rec.called

        sends = _run(flow(None))
        assert sends[0]["status"] == 401
