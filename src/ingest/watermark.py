"""Incremental watermarks stored in ``ctl.watermark``.

The watermark is advanced inside the *same transaction* that lands the raw
rows, so a crash between landing and committing cannot skip data. The pipeline
is therefore at-least-once and relies on downstream idempotent de-duplication.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from src.common.logging import get_logger, log_event

logger = get_logger("fleet.ingest.watermark")


def read_watermark(source: str, conn: psycopg.Connection) -> datetime | None:
    """Return the last committed cursor timestamp for ``source`` (or ``None``)."""
    with conn.cursor() as cur:
        cur.execute("SELECT last_cursor_ts FROM ctl.watermark WHERE source_name = %s", (source,))
        row = cur.fetchone()
    return row[0] if row else None


def advance_watermark(source: str, ts: datetime, run_id: str, conn: psycopg.Connection) -> None:
    """Advance the watermark monotonically. Must be called inside the landing txn."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ctl.watermark (source_name, last_cursor_ts, last_run_id, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (source_name) DO UPDATE
                SET last_cursor_ts = GREATEST(ctl.watermark.last_cursor_ts, EXCLUDED.last_cursor_ts),
                    last_run_id    = EXCLUDED.last_run_id,
                    updated_at     = now()
            """,
            (source, ts, run_id),
        )
    log_event(logger, "watermark advanced", source=source, cursor_ts=ts.isoformat())


def reset_watermark(source: str, conn: psycopg.Connection) -> None:
    """Delete the watermark so the next run backfills from the beginning."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ctl.watermark WHERE source_name = %s", (source,))
