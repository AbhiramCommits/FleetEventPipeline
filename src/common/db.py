"""Small psycopg3 helpers used across the pipeline.

The functions deliberately return plain tuples / dicts so callers stay close to
the SQL without an ORM layer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row

from src.common.config import get_settings

logger = logging.getLogger("fleet.db")


def get_conn(autocommit: bool = False) -> psycopg.Connection:
    """Open a new PostgreSQL connection using ``DB_DSN``."""
    settings = get_settings()
    return psycopg.connect(settings.db_dsn, autocommit=autocommit)


def run_sql_file(path: str | Path, conn: psycopg.Connection | None = None) -> None:
    """Execute a ``.sql`` file (possibly many statements) inside one transaction."""
    sql = Path(path).read_text()
    owns_conn = conn is None
    connection = conn or get_conn()
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
        if owns_conn:
            connection.commit()
    except Exception:
        if owns_conn:
            connection.rollback()
        raise
    finally:
        if owns_conn:
            connection.close()


def fetch_all(
    sql: str,
    params: Sequence[Any] | None = None,
    conn: psycopg.Connection | None = None,
) -> list[tuple]:
    """Run a query and return every row as a tuple."""
    owns_conn = conn is None
    connection = conn or get_conn()
    try:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        if owns_conn:
            connection.close()


def fetch_dicts(
    sql: str,
    params: Sequence[Any] | None = None,
    conn: psycopg.Connection | None = None,
) -> list[dict[str, Any]]:
    """Run a query and return rows keyed by column name."""
    owns_conn = conn is None
    connection = conn or get_conn()
    try:
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        if owns_conn:
            connection.close()


def execute_many(
    sql: str,
    rows: Iterable[Sequence[Any]],
    conn: psycopg.Connection,
    batch_size: int = 1000,
) -> int:
    """Batched ``executemany`` helper; returns number of rows submitted."""
    total = 0
    batch: list[Sequence[Any]] = []
    with conn.cursor() as cur:
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                cur.executemany(sql, batch)
                total += len(batch)
                batch.clear()
        if batch:
            cur.executemany(sql, batch)
            total += len(batch)
    return total
