"""DuckDB connection and SQL steps 01 to 03."""

from __future__ import annotations

import duckdb
import pandas as pd

from pipeline.config import DB_PATH, L1_ENGLISH, SQL_DIR


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def run_sql(con: duckdb.DuckDBPyConnection, name: str) -> None:
    con.execute((SQL_DIR / name).read_text())


def build(con: duckdb.DuckDBPyConnection) -> None:
    """01_ingest, 02_clean and 03_metrics."""
    run_sql(con, "01_ingest.sql")
    l1 = pd.DataFrame({"l1_zh": list(L1_ENGLISH), "l1_en": list(L1_ENGLISH.values())})
    con.execute("CREATE OR REPLACE TABLE l1_names AS SELECT * FROM l1")
    run_sql(con, "02_clean.sql")
    run_sql(con, "03_metrics.sql")
