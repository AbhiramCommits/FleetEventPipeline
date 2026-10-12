"""ELT orchestration: build the mart star schema from staging.

Runs the transform SQL in dependency order (dimensions, then facts) inside one
transaction per layer and records row counts on ``ctl.pipeline_run``.

Usage::

    python -m src.elt.run
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any

from src.common.config import get_settings
from src.common.db import get_conn
from src.common.logging import get_logger, log_event, set_run_id

logger = get_logger("fleet.elt")

# (layer name, file) in dependency order.
LAYERS: list[tuple[str, str]] = [
    ("dimensions", "sql/02_elt_dimensions.sql"),
    ("facts", "sql/02_elt_facts.sql"),
]

MART_TABLES = [
    "mart.dim_vehicle",
    "mart.dim_operator",
    "mart.dim_geofence",
    "mart.dim_date",
    "mart.dim_time_of_day",
    "mart.fact_gps_ping",
    "mart.fact_trip_segment",
    "mart.fact_idle_event",
    "mart.fact_geofence_transition",
]

FACT_TABLES = [
    "mart.fact_gps_ping",
    "mart.fact_trip_segment",
    "mart.fact_idle_event",
    "mart.fact_geofence_transition",
]


def run_elt() -> dict[str, Any]:
    """Execute the ELT layers and return row counts plus the run id."""
    root = get_settings().repo_root
    conn = get_conn(autocommit=True)
    run_id = uuid.uuid4()
    set_run_id(str(run_id))
    started = time.perf_counter()
    conn.execute(
        "INSERT INTO ctl.pipeline_run (run_id, job_name, status) VALUES (%s, 'elt', 'running')",
        (run_id,),
    )
    try:
        for layer, rel in LAYERS:
            sql = (root / rel).read_text()
            layer_start = time.perf_counter()
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql)
            log_event(logger, "layer complete", layer=layer, elapsed_sec=round(time.perf_counter() - layer_start, 3))

        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in MART_TABLES}
        elapsed = round(time.perf_counter() - started, 3)
        fact_rows = sum(counts[t] for t in FACT_TABLES)
        conn.execute(
            """
            UPDATE ctl.pipeline_run
               SET finished_at = now(), status = 'succeeded', rows_read = (
                       SELECT count(*) FROM stg.gps_ping
                   ), rows_written = %s, notes = %s
             WHERE run_id = %s
            """,
            (fact_rows, f"mart_rows={sum(counts.values())} elapsed_sec={elapsed}", run_id),
        )
        log_event(logger, "elt complete", run_id=str(run_id), fact_rows=fact_rows, elapsed_sec=elapsed)
        return {"run_id": str(run_id), "counts": counts, "fact_rows": fact_rows, "elapsed_sec": elapsed}
    except Exception as exc:  # noqa: BLE001
        conn.execute(
            "UPDATE ctl.pipeline_run SET finished_at = now(), status = 'failed', notes = %s WHERE run_id = %s",
            (str(exc), run_id),
        )
        log_event(logger, "elt failed", error=str(exc))
        raise
    finally:
        conn.close()


def main() -> int:
    """CLI entrypoint."""
    result = run_elt()
    for table, count in result["counts"].items():
        log_event(logger, "mart table", table=table, rows=count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
