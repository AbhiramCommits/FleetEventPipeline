"""Data-quality gates.

Each gate writes a row to ``ctl.dq_result`` and the process exits non-zero if
any gate with ``severity = 'error'`` fails. Gates:

1. freshness           2. duplicate rate        3. invalid-GPS reject rate
4. referential integrity  5. unmatched geofence exits
6. row-count drift     7. watermark monotonicity

Usage::

    python -m src.dq.checks
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from tabulate import tabulate

from src.common.config import get_settings
from src.common.db import get_conn
from src.common.logging import get_logger, log_event, set_run_id
from src.dq import alert

logger = get_logger("fleet.dq")

INVALID_GPS_REASONS = ("MISSING_GPS", "NULL_ISLAND", "OUT_OF_RANGE_LATLON")
STALE_VEHICLE_HOURS = 24


def _gate(
    name: str,
    passed: bool,
    observed: float,
    threshold: float,
    detail: str,
    severity: str = "error",
) -> dict[str, Any]:
    return {
        "check_name": name,
        "severity": severity,
        "passed": bool(passed),
        "observed": round(float(observed), 4),
        "threshold": round(float(threshold), 4),
        "detail": detail,
    }


def check_freshness(conn: psycopg.Connection, lookback_hours: int) -> dict[str, Any]:
    """Max staged ping timestamp must be within the lookback threshold."""
    row = conn.execute("SELECT max(ts) FROM stg.gps_ping").fetchone()
    newest = row[0]
    if newest is None:
        return _gate("freshness", False, 10**6, lookback_hours, "no staged pings", "error")
    age_hours = (datetime.now(UTC) - newest).total_seconds() / 3600
    stale = conn.execute(
        f"""
        SELECT vehicle_id, round((extract(epoch FROM (now() - max(ts))) / 3600)::numeric, 1) AS age_h
        FROM stg.gps_ping GROUP BY vehicle_id
        HAVING (now() - max(ts)) > interval '{STALE_VEHICLE_HOURS} hours'
        ORDER BY age_h DESC LIMIT 10
        """
    ).fetchall()
    stale_desc = ", ".join(f"{v}:{a}h" for v, a in stale) or "none"
    passed = age_hours <= lookback_hours
    return _gate(
        "freshness",
        passed,
        age_hours,
        lookback_hours,
        f"newest={newest.isoformat()} stale_vehicles({len(stale)})={stale_desc}",
    )


def check_duplicate_rate(conn: psycopg.Connection) -> dict[str, Any]:
    """Duplicates blocked / total events must be below 5%."""
    row = conn.execute(
        "SELECT rows_deduped, rows_read FROM ctl.pipeline_run "
        "WHERE job_name = 'fleet_ingest' AND status = 'succeeded' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    deduped, total = (row if row else (0, 0))
    rate = (deduped / total * 100) if total else 0.0
    return _gate("duplicate_rate", rate < 5.0, rate, 5.0, f"deduped={deduped} total={total}")


def check_invalid_gps_rate(conn: psycopg.Connection) -> dict[str, Any]:
    """Invalid-GPS rejects / total landed GPS events must be below 3%."""
    rejects = conn.execute(
        "SELECT count(*) FROM ctl.reject WHERE reason_code = ANY(%s)", (list(INVALID_GPS_REASONS),)
    ).fetchone()[0]
    total = conn.execute("SELECT count(*) FROM raw.gps_ping_raw").fetchone()[0]
    rate = (rejects / total * 100) if total else 0.0
    return _gate("invalid_gps_reject_rate", rate < 3.0, rate, 3.0, f"rejects={rejects} total={total}")


def check_referential_integrity(conn: psycopg.Connection) -> dict[str, Any]:
    """Every non-null fact dim key must exist in its dimension."""
    violations = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM mart.fact_gps_ping f
             WHERE NOT EXISTS (SELECT 1 FROM mart.dim_vehicle d WHERE d.vehicle_key = f.vehicle_key)
                OR (f.operator_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mart.dim_operator d WHERE d.operator_key = f.operator_key))
                OR (f.geofence_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mart.dim_geofence d WHERE d.geofence_key = f.geofence_key)))
        + (SELECT count(*) FROM mart.fact_trip_segment f
             WHERE NOT EXISTS (SELECT 1 FROM mart.dim_vehicle d WHERE d.vehicle_key = f.vehicle_key))
        + (SELECT count(*) FROM mart.fact_idle_event f
             WHERE NOT EXISTS (SELECT 1 FROM mart.dim_vehicle d WHERE d.vehicle_key = f.vehicle_key))
        + (SELECT count(*) FROM mart.fact_geofence_transition f
             WHERE NOT EXISTS (SELECT 1 FROM mart.dim_vehicle d WHERE d.vehicle_key = f.vehicle_key)
                OR NOT EXISTS (SELECT 1 FROM mart.dim_geofence d WHERE d.geofence_key = f.geofence_key))
        """
    ).fetchone()[0]
    return _gate("referential_integrity", violations == 0, violations, 0.0, f"unmatched_fact_rows={violations}")


def check_unmatched_geofence_exits(conn: psycopg.Connection) -> dict[str, Any]:
    """Unmatched geofence exits / total exits must be below 1%."""
    unmatched = conn.execute(
        "SELECT count(*) FROM mart.fact_geofence_transition WHERE entered_at IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT count(*) FROM stg.geofence_event WHERE event_type = 'exit'").fetchone()[0]
    rate = (unmatched / total * 100) if total else 0.0
    return _gate("unmatched_geofence_exits", rate < 1.0, rate, 1.0, f"unmatched={unmatched} exits={total}")


def check_row_count_drift(conn: psycopg.Connection, current_run_id: uuid.UUID) -> dict[str, Any]:
    """Fact row count must not drift more than ±40% vs the previous ELT run."""
    current = conn.execute("SELECT count(*) FROM mart.fact_gps_ping").fetchone()[0]
    prev = conn.execute(
        """
        SELECT rows_written FROM ctl.pipeline_run
        WHERE job_name = 'elt' AND status = 'succeeded' AND run_id <> %s
        ORDER BY started_at DESC LIMIT 1
        """,
        (current_run_id,),
    ).fetchone()
    if not prev or not prev[0]:
        return _gate("row_count_drift", True, 0.0, 40.0, f"no baseline; current={current}", "warning")
    previous = prev[0]
    drift = abs(current - previous) / previous * 100
    return _gate("row_count_drift", drift <= 40.0, drift, 40.0, f"current={current} previous={previous}")


def check_watermark_monotonicity(conn: psycopg.Connection) -> dict[str, Any]:
    """Stored watermark must not be behind the newest raw event per source."""
    sources = {"gps": "raw.gps_ping_raw", "geofence": "raw.geofence_event_raw"}
    violations = 0
    details: list[str] = []
    for source, table in sources.items():
        row = conn.execute(
            f"""
            SELECT w.last_cursor_ts, max((r.payload->>'ts')::timestamptz) AS max_raw
            FROM ctl.watermark w
            LEFT JOIN {table} r ON true
            WHERE w.source_name = %s
            GROUP BY w.last_cursor_ts
            """,
            (source,),
        ).fetchone()
        if row and row[0] is not None and row[1] is not None and row[0] < row[1]:
            violations += 1
            details.append(f"{source}: wm={row[0]} < max_raw={row[1]}")
    return _gate(
        "watermark_monotonicity",
        violations == 0,
        violations,
        0.0,
        "; ".join(details) or "all watermarks at or ahead of raw max",
    )


def run_checks() -> tuple[uuid.UUID, list[dict[str, Any]]]:
    """Run every gate, persist results and return (run_id, results)."""
    settings = get_settings()
    conn = get_conn(autocommit=True)
    run_id = uuid.uuid4()
    set_run_id(str(run_id))
    conn.execute(
        "INSERT INTO ctl.pipeline_run (run_id, job_name, status) VALUES (%s, 'dq', 'running')", (run_id,)
    )

    checks: list[Callable[[], dict[str, Any]]] = [
        lambda: check_freshness(conn, settings.lookback_hours),
        lambda: check_duplicate_rate(conn),
        lambda: check_invalid_gps_rate(conn),
        lambda: check_referential_integrity(conn),
        lambda: check_unmatched_geofence_exits(conn),
        lambda: check_row_count_drift(conn, run_id),
        lambda: check_watermark_monotonicity(conn),
    ]

    results: list[dict[str, Any]] = []
    for check in checks:
        result = check()
        results.append(result)
        conn.execute(
            """
            INSERT INTO ctl.dq_result (run_id, check_name, severity, passed, observed, threshold, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                result["check_name"],
                result["severity"],
                result["passed"],
                result["observed"],
                result["threshold"],
                result["detail"],
            ),
        )

    failures = [r for r in results if not r["passed"] and r["severity"] == "error"]
    status = "succeeded" if not failures else "failed"
    conn.execute(
        "UPDATE ctl.pipeline_run SET finished_at = now(), status = %s, rows_written = %s, notes = %s WHERE run_id = %s",
        (status, len(results), f"failed={len(failures)}", run_id),
    )
    conn.close()
    return run_id, results


def main() -> int:
    """CLI entrypoint. Returns 1 if any error-severity gate failed."""
    run_id, results = run_checks()
    print(
        tabulate(
            [[r["check_name"], r["severity"], "PASS" if r["passed"] else "FAIL", r["observed"], r["threshold"]]
             for r in results],
            headers=["check", "severity", "result", "observed", "threshold"],
            tablefmt="github",
        )
    )
    failures = [r for r in results if not r["passed"] and r["severity"] == "error"]
    if failures:
        alert.write_alert(str(run_id), failures)
        log_event(logger, "dq failed", run_id=str(run_id), failures=len(failures))
        return 1
    log_event(logger, "dq passed", run_id=str(run_id), checks=len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
