"""P0-B :: paper account state must survive restarts and crashes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import brokers.paper_broker as pb
from brokers.paper_broker import PaperBroker, PaperStateError

SL, TP, QTY = 2390.0, 2430.0, 0.1


def _mk(cfg, monkeypatch, price=2400.0):
    b = PaperBroker(cfg)
    monkeypatch.setattr(b, "last_price", lambda: price)
    return b


class TestRestartPersistence:
    def test_open_position_survives_restart(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        res = b1.market_order("LONG", QTY, SL, TP, "LONDON_NY_OVERLAP", "t")
        b1.modify_sl(res.ticket, 2392.0)

        b2 = _mk(mk_cfg, monkeypatch)   # simulated process restart
        assert b2.balance() == pytest.approx(b1.balance())
        pos = b2.open_positions()
        assert len(pos) == 1
        assert pos[0].ticket == res.ticket
        assert pos[0].entry == pytest.approx(res.price)
        assert pos[0].sl == pytest.approx(2392.0), "SL move must persist"
        assert pos[0].tp == pytest.approx(TP)

    def test_closed_pnl_survives_restart(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        res = b1.market_order("LONG", QTY, SL, TP, "S", "t")
        pnl = b1.close_position(res.ticket, reason="TP", intended_price=TP)
        assert pnl is not None

        b2 = _mk(mk_cfg, monkeypatch)
        assert b2.realized_pnl == pytest.approx(pnl)
        assert b2.balance() == pytest.approx(mk_cfg.starting_balance + pnl)
        assert len(b2.closed_trades) == 1
        assert b2.closed_trades[0]["reason"] == "TP"
        assert b2.open_positions() == []

    def test_state_file_carries_full_contract(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        res = b1.market_order("LONG", QTY, SL, TP, "S", "t")
        b1.close_position(res.ticket, reason="SL", intended_price=SL)
        raw = json.loads(pb.ACCOUNT_FILE.read_text())
        for key in ("schema_version", "starting_balance", "cash_balance",
                    "equity", "fees_paid", "realized_pnl", "open_positions",
                    "closed_trades", "updated_at"):
            assert key in raw, f"state file missing {key}"
        assert raw["schema_version"] == pb.SCHEMA_VERSION

    def test_heartbeat_save_flushes(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        b1.market_order("LONG", QTY, SL, TP, "S", "t")
        pb.ACCOUNT_FILE.unlink(missing_ok=True)   # prove the flush rewrites
        b1.heartbeat_save()
        assert pb.ACCOUNT_FILE.exists()
        raw = json.loads(pb.ACCOUNT_FILE.read_text())
        assert len(raw["open_positions"]) == 1

    def test_disconnect_flushes_on_sigterm_path(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        b1.market_order("LONG", QTY, SL, TP, "S", "t")
        pb.ACCOUNT_FILE.unlink(missing_ok=True)
        b1.disconnect()   # bot shutdown calls this
        assert pb.ACCOUNT_FILE.exists()


class TestRefuseToStart:
    def test_corrupt_file_refuses_and_backs_up(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        b1.market_order("LONG", QTY, SL, TP, "S", "t")
        pb.ACCOUNT_FILE.write_text("{ this is not json !!!")

        with pytest.raises(PaperStateError) as exc:
            PaperBroker(mk_cfg)
        assert str(pb.ACCOUNT_FILE) in str(exc.value), \
            "error must name the exact path"
        bak = pb.ACCOUNT_FILE.with_suffix(".json.bak")
        assert bak.exists(), "corrupt file must be backed up"
        assert "not json" in bak.read_text()

    def test_unknown_schema_refuses_untouched(self, mk_cfg, monkeypatch):
        payload = {"schema_version": 99, "cash_balance": 9999.0}
        pb.ACCOUNT_FILE.write_text(json.dumps(payload))
        with pytest.raises(PaperStateError) as exc:
            PaperBroker(mk_cfg)
        assert "schema_version" in str(exc.value)
        assert json.loads(pb.ACCOUNT_FILE.read_text()) == payload, \
            "refusal must never overwrite the file"

    def test_unreadable_fields_refuse(self, mk_cfg, monkeypatch):
        pb.ACCOUNT_FILE.write_text(json.dumps(
            {"schema_version": 1, "cash_balance": "not-a-number"}))
        with pytest.raises(PaperStateError):
            PaperBroker(mk_cfg)

    def test_missing_file_initializes_fresh(self, mk_cfg, monkeypatch):
        b = _mk(mk_cfg, monkeypatch)
        assert b.balance() == mk_cfg.starting_balance
        assert b.open_positions() == []
        assert pb.ACCOUNT_FILE.exists(), "fresh state is written immediately"


class TestCrashSafety:
    def test_crash_mid_write_keeps_old_state(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        res = b1.market_order("LONG", QTY, SL, TP, "S", "t")
        good = pb.ACCOUNT_FILE.read_text()

        real_replace = __import__("os").replace

        def exploding_replace(src, dst):
            raise OSError("simulated crash mid-write")

        monkeypatch.setattr(pb.os, "replace", exploding_replace)
        b1.close_position(res.ticket, reason="TP", intended_price=TP)
        monkeypatch.setattr(pb.os, "replace", real_replace)

        # old state intact, no tmp litter, restart sees the OLD truth
        assert pb.ACCOUNT_FILE.read_text() == good
        assert not list(pb.ACCOUNT_FILE.parent.glob("*.tmp"))
        b2 = _mk(mk_cfg, monkeypatch)
        assert b2.open_positions()[0].ticket == res.ticket

    def test_no_tmp_files_after_clean_save(self, mk_cfg, monkeypatch):
        b1 = _mk(mk_cfg, monkeypatch)
        b1.market_order("LONG", QTY, SL, TP, "S", "t")
        assert not list(pb.ACCOUNT_FILE.parent.glob("*.tmp"))

    def test_duckdb_mirror_receives_closes(self, mk_cfg, monkeypatch):
        pytest.importorskip("duckdb")
        b1 = _mk(mk_cfg, monkeypatch)
        res = b1.market_order("LONG", QTY, SL, TP, "S", "t")
        b1.close_position(res.ticket, reason="SL", intended_price=SL)
        import duckdb

        con = duckdb.connect(str(pb.DUCKDB_FILE))
        try:
            rows = con.execute(
                "SELECT ticket, reason FROM paper_trades").fetchall()
        finally:
            con.close()
        assert rows == [(res.ticket, "SL")]


class TestPathBindingRegression:
    def test_instance_paths_survive_module_repoint(self, mk_cfg, monkeypatch,
                                                   tmp_path):
        """Regression: the atexit flush used to resolve the module-global
        ACCOUNT_FILE at interpreter exit - after pytest's monkeypatch
        teardown had re-pointed it, leaking test state into the real
        data/. Paths are now bound at construction."""
        b = _mk(mk_cfg, monkeypatch)
        real_file = pb.ACCOUNT_FILE
        monkeypatch.setattr(pb, "ACCOUNT_FILE",
                            tmp_path / "elsewhere.json")
        assert b.account_path == real_file, \
            "an existing instance must keep its construction-time path"

    def test_no_repo_state_leak_after_suite(self):
        """The repo's real data/ must stay clean of MONEY test artifacts.
        (Import-time side effects like data/store/ and reaper.log are
        benign and gitignored - not money state.)"""
        repo_data = Path(__file__).resolve().parents[1] / "data"
        leaked = [f.name for f in repo_data.glob("paper_account.json*")]
        leaked += [f.name for f in repo_data.glob("paper.duckdb")]
        leaked += [f.name for f in repo_data.glob("account_state.json")]
        assert not leaked, \
            f"test money-state leaked into the repo: {leaked}"
