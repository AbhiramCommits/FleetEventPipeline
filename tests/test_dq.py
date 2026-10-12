"""DQ gate tests: each gate fails on a crafted bad dataset, passes on good data.

All mutations happen inside a transaction that is rolled back.
"""

from __future__ import annotations

import uuid

from src.dq.checks import (
    check_duplicate_rate,
    check_freshness,
    check_invalid_gps_rate,
    check_referential_integrity,
    check_row_count_drift,
    check_unmatched_geofence_exits,
    check_watermark_monotonicity,
)
from tests.conftest import Rollback


def test_freshness_gate(conn) -> None:
    try:
        with conn.transaction():
            conn.execute("DELETE FROM stg.gps_ping")
            conn.execute(
                "INSERT INTO stg.gps_ping (vendor_event_id, vehicle_id, ts, lat, lon) VALUES ('F1','V1', now(), 1, 1)"
            )
            assert check_freshness(conn, 720)["passed"]
            conn.execute("UPDATE stg.gps_ping SET ts = now() - interval '100 hours'")
            assert not check_freshness(conn, 24)["passed"]
            raise Rollback
    except Rollback:
        pass


def test_duplicate_rate_gate(conn) -> None:
    run_id = uuid.uuid4()
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO ctl.pipeline_run (run_id, job_name, status, rows_deduped, rows_read, started_at) "
                "VALUES (%s, 'fleet_ingest', 'succeeded', 10, 100, now())",
                (run_id,),
            )
            assert not check_duplicate_rate(conn)["passed"], "10% duplicates should fail"
            conn.execute("UPDATE ctl.pipeline_run SET rows_deduped = 1 WHERE run_id = %s", (run_id,))
            assert check_duplicate_rate(conn)["passed"], "1% duplicates should pass"
            raise Rollback
    except Rollback:
        pass


def test_invalid_gps_rate_gate(conn) -> None:
    try:
        with conn.transaction():
            conn.execute("DELETE FROM raw.gps_ping_raw")
            conn.execute("INSERT INTO raw.gps_ping_raw (payload) VALUES ('{}')")
            conn.execute("INSERT INTO ctl.reject (source_name, reason_code) VALUES ('gps', 'MISSING_GPS')")
            assert not check_invalid_gps_rate(conn)["passed"], "100% invalid should fail"
            conn.execute("DELETE FROM ctl.reject")
            assert check_invalid_gps_rate(conn)["passed"], "0% invalid should pass"
            raise Rollback
    except Rollback:
        pass


def test_referential_integrity_gate(conn) -> None:
    try:
        with conn.transaction():
            assert check_referential_integrity(conn)["passed"]
            conn.execute(
                "INSERT INTO mart.fact_gps_ping (vendor_event_id, vehicle_key, date_key, time_key, event_ts) "
                "VALUES ('BAD-KEY', 999999, 20260101, 0, now())"
            )
            assert not check_referential_integrity(conn)["passed"]
            raise Rollback
    except Rollback:
        pass


def test_unmatched_geofence_exits_gate(conn) -> None:
    try:
        with conn.transaction():
            conn.execute("DELETE FROM stg.geofence_event")
            conn.execute(
                "INSERT INTO stg.geofence_event (vendor_event_id, vehicle_id, geofence_id, event_type, ts) "
                "VALUES ('E1','V1','G1','exit', now())"
            )
            conn.execute("DELETE FROM mart.fact_geofence_transition")
            conn.execute(
                "INSERT INTO mart.fact_geofence_transition (vehicle_key, geofence_key, entered_at, exited_at, unmatched_flag) "
                "VALUES (1, 1, NULL, now(), true)"
            )
            assert not check_unmatched_geofence_exits(conn)["passed"], "100% unmatched should fail"
            conn.execute(
                "UPDATE mart.fact_geofence_transition SET entered_at = now() - interval '10 minutes'"
            )
            assert check_unmatched_geofence_exits(conn)["passed"], "0% unmatched should pass"
            raise Rollback
    except Rollback:
        pass


def test_row_count_drift_gate(conn) -> None:
    run_id = uuid.uuid4()
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO ctl.pipeline_run (run_id, job_name, status, rows_written, started_at) "
                "VALUES (%s, 'elt', 'succeeded', 1, now())",
                (run_id,),
            )
            assert not check_row_count_drift(conn, uuid.uuid4())["passed"], "huge drift should fail"
            conn.execute(
                "UPDATE ctl.pipeline_run SET rows_written = (SELECT count(*) FROM mart.fact_gps_ping) WHERE run_id = %s",
                (run_id,),
            )
            assert check_row_count_drift(conn, uuid.uuid4())["passed"], "zero drift should pass"
            raise Rollback
    except Rollback:
        pass


def test_watermark_monotonicity_gate(conn) -> None:
    try:
        with conn.transaction():
            conn.execute("DELETE FROM ctl.watermark")
            conn.execute("INSERT INTO ctl.watermark (source_name, last_cursor_ts) VALUES ('gps', '2000-01-01')")
            assert not check_watermark_monotonicity(conn)["passed"], "watermark behind raw should fail"
            conn.execute("UPDATE ctl.watermark SET last_cursor_ts = now() + interval '1 day' WHERE source_name = 'gps'")
            # geofence watermark is absent -> treated as no violation.
            assert check_watermark_monotonicity(conn)["passed"]
            raise Rollback
    except Rollback:
        pass
