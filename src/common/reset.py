"""Reset pipeline data (not schema) for a clean, reproducible ``make demo``.

Truncates raw, staging, control and mart data but leaves migrations in place.
Usage::

    python -m src.common.reset
"""

from __future__ import annotations

import sys

from src.common.db import get_conn
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.reset")

TABLES = [
    "raw.gps_ping_raw",
    "raw.geofence_event_raw",
    "stg.gps_ping",
    "stg.geofence_event",
    "stg.vehicle",
    "stg.operator",
    "stg.geofence",
    "ctl.watermark",
    "ctl.pipeline_run",
    "ctl.reject",
    "ctl.dq_result",
    "mart.fact_gps_ping",
    "mart.fact_trip_segment",
    "mart.fact_idle_event",
    "mart.fact_geofence_transition",
    "mart.dim_vehicle",
    "mart.dim_operator",
    "mart.dim_geofence",
    "mart.dim_date",
    "mart.dim_time_of_day",
]


def reset_data() -> None:
    """Truncate all pipeline data tables with RESTART IDENTITY."""
    conn = get_conn(autocommit=True)
    try:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    finally:
        conn.close()
    log_event(logger, "pipeline data reset")


def main() -> int:
    reset_data()
    return 0


if __name__ == "__main__":
    sys.exit(main())
