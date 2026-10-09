"""
GOLD REAPER APEX :: Feature Store (DuckDB over columnar Parquet)
================================================================
One queryable home for every bar, tick, feature, label and calendar row.
Idempotent writes: overlapping ranges are replaced, never duplicated.

Usage:
    from features.store import FeatureStore
    store = FeatureStore()                       # data/store/apex.duckdb
    store.write_bars("1h", df)                   # df: time-indexed OHLCV
    df = store.read_bars("1h", start="2024-01-01")
    store.write_table("features_1h", df)
    df = store.query("SELECT * FROM features_1h WHERE rsi_14 < 30")
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "store"
STORE_DIR.mkdir(parents=True, exist_ok=True)

VALID_TFS = {"1m", "2m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk", "1mo", "3mo", "1y"}


class FeatureStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (STORE_DIR / "apex.duckdb")
        self._con = None

    # ------------------------------------------------------------ connection
    @property
    def con(self):
        if self._con is None:
            import duckdb
            self._con = duckdb.connect(str(self.path))
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # ------------------------------------------------------------ generic
    def write_table(self, name: str, df: pd.DataFrame,
                    replace: bool = False) -> int:
        """Write a DataFrame as a table. Idempotent by 'time' column if present."""
        if df is None or df.empty:
            return 0
        d = df.copy()
        if "time" in d.columns:
            d["time"] = pd.to_datetime(d["time"], utc=True, format="mixed")
        elif isinstance(d.index, pd.DatetimeIndex):
            d = d.reset_index()
            # reset_index names the column after the index (often not "time")
            idx_col = d.columns[0]
            if idx_col != "time":
                d = d.rename(columns={idx_col: "time"})
            d["time"] = pd.to_datetime(d["time"], utc=True, format="mixed")
        self.con.register("apex_tmp", d)
        try:
            if replace:
                self.con.execute(f"DROP TABLE IF EXISTS {name}")
                self.con.execute(f"CREATE TABLE {name} AS SELECT * FROM apex_tmp")
            elif "time" in d.columns and self._table_exists(name):
                tmin, tmax = d["time"].min(), d["time"].max()
                self.con.execute(
                    f"DELETE FROM {name} WHERE time BETWEEN ? AND ?", [tmin, tmax])
                self.con.execute(f"INSERT INTO {name} SELECT * FROM apex_tmp")
            else:
                self.con.execute(f"CREATE TABLE IF NOT EXISTS {name} AS SELECT * FROM apex_tmp")
        finally:
            self.con.unregister("apex_tmp")
        return len(d)

    def read_table(self, name: str, start=None, end=None) -> pd.DataFrame:
        if not self._table_exists(name):
            return pd.DataFrame()
        where, args = [], []
        if start is not None:
            where.append("time >= ?")
            args.append(pd.Timestamp(start, tz="UTC") if not isinstance(start, pd.Timestamp)
                        else start)
        if end is not None:
            where.append("time <= ?")
            args.append(pd.Timestamp(end, tz="UTC") if not isinstance(end, pd.Timestamp)
                        else end)
        sql = f"SELECT * FROM {name}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY time"
        df = self.con.execute(sql, args).fetchdf()
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
            df = df.set_index("time")
        return df

    def query(self, sql: str, args: list | None = None) -> pd.DataFrame:
        return self.con.execute(sql, args or []).fetchdf()

    def tables(self) -> list[str]:
        rows = self.con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'").fetchall()
        return sorted(r[0] for r in rows)

    def _table_exists(self, name: str) -> bool:
        return name in self.tables()

    # ------------------------------------------------------------ bars API
    def write_bars(self, tf: str, df: pd.DataFrame) -> int:
        if tf not in VALID_TFS:
            raise ValueError(f"invalid timeframe {tf}; valid: {sorted(VALID_TFS)}")
        return self.write_table(f"bars_{tf}", df)

    def read_bars(self, tf: str, start=None, end=None) -> pd.DataFrame:
        return self.read_table(f"bars_{tf}", start, end)

    def export_parquet(self, name: str, out_dir: Path | None = None) -> Path:
        out = (out_dir or STORE_DIR) / f"{name}.parquet"
        self.con.execute(
            f"COPY (SELECT * FROM {name}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        return out

    def stats(self) -> pd.DataFrame:
        rows = []
        for t in self.tables():
            try:
                n = self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                tmin = tmax = "-"
                cols = [c[0] for c in self.con.execute(f"DESCRIBE {t}").fetchall()]
                if "time" in cols:
                    r = self.con.execute(
                        f"SELECT MIN(time), MAX(time) FROM {t}").fetchone()
                    tmin, tmax = str(r[0])[:10], str(r[1])[:10]
                rows.append({"table": t, "rows": n, "from": tmin, "to": tmax})
            except Exception:  # noqa: BLE001
                continue
        return pd.DataFrame(rows)
