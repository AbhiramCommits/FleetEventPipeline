"""ELT SQL tests: trip and idle segmentation against hand-built fixture pings.

Every test runs inside a transaction that is deliberately rolled back, so the
production mart is left untouched. Staging is emptied inside the transaction and
replaced with a tiny, fully-known fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import Rollback

D0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _insert_fixture(conn) -> None:
    conn.execute("DELETE FROM stg.gps_ping")
    conn.execute("DELETE FROM stg.geofence_event")
    conn.execute("DELETE FROM stg.vehicle")
    conn.execute("DELETE FROM stg.operator")
    conn.execute("DELETE FROM stg.geofence")
    conn.execute(
        "INSERT INTO stg.vehicle (vehicle_id, name, asset_type, home_yard) VALUES ('TEST-VEH','Test','excavator','YARD-01')"
    )
    rows = []
    # 4 moving pings, 1 minute apart, 30 mph, drifting north.
    for i in range(4):
        ts = D0.replace(minute=0 + i)
        rows.append((f"M{i}", "TEST-VEH", ts, 39.0 + i * 0.001, -94.5, 30.0))
    # 7 stationary engine-on pings from 10:10 to 10:16 -> a 6-minute idle event.
    for i in range(7):
        ts = datetime(2026, 1, 1, 10, 10 + i, tzinfo=UTC)
        rows.append((f"I{i}", "TEST-VEH", ts, 39.0, -94.5, 0.0))
    for vid, vehicle, ts, lat, lon, speed in rows:
        conn.execute(
            "INSERT INTO stg.gps_ping (vendor_event_id, vehicle_id, ts, lat, lon, speed_mph, speed_kph, engine_on) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, true)",
            (vid, vehicle, ts, lat, lon, speed, speed * 1.609344),
        )


def _run_elt_sql(conn, repo_root) -> None:
    for rel in ("sql/02_elt_dimensions.sql", "sql/02_elt_facts.sql"):
        conn.execute((repo_root / rel).read_text())


def test_trip_segmentation_known_answer(conn) -> None:
    """The 4 moving pings form exactly one trip: 4 pings, avg/max 48.28 kph."""
    from src.common.config import get_settings

    root = get_settings().repo_root
    try:
        with conn.transaction():
            _insert_fixture(conn)
            _run_elt_sql(conn, root)
            trips = conn.execute(
                "SELECT f.ping_count, f.avg_speed_kph, f.max_speed_kph, f.distance_km "
                "FROM mart.fact_trip_segment f JOIN mart.dim_vehicle dv USING (vehicle_key) "
                "WHERE dv.vehicle_id = 'TEST-VEH'"
            ).fetchall()
            assert len(trips) == 1, trips
            ping_count, avg_speed, max_speed, distance = trips[0]
            assert ping_count == 4
            assert float(avg_speed) == 48.28
            assert float(max_speed) == 48.28
            assert float(distance) > 0
            raise Rollback
    except Rollback:
        pass


def test_idle_segmentation_known_answer(conn) -> None:
    """The 7 stationary engine-on pings form exactly one 6-minute idle event."""
    from src.common.config import get_settings

    root = get_settings().repo_root
    try:
        with conn.transaction():
            _insert_fixture(conn)
            _run_elt_sql(conn, root)
            idles = conn.execute(
                "SELECT f.duration_min FROM mart.fact_idle_event f JOIN mart.dim_vehicle dv USING (vehicle_key) "
                "WHERE dv.vehicle_id = 'TEST-VEH'"
            ).fetchall()
            assert len(idles) == 1, idles
            assert float(idles[0][0]) == 6.0
            raise Rollback
    except Rollback:
        pass


def test_flags_moving_and_idling(conn) -> None:
    """Point-level moving/idling flags match the fixture (4 moving, 7 idling)."""
    from src.common.config import get_settings

    root = get_settings().repo_root
    try:
        with conn.transaction():
            _insert_fixture(conn)
            _run_elt_sql(conn, root)
            moving, idling = conn.execute(
                "SELECT count(*) FILTER (WHERE f.is_moving), count(*) FILTER (WHERE f.is_idling) "
                "FROM mart.fact_gps_ping f JOIN mart.dim_vehicle dv USING (vehicle_key) "
                "WHERE dv.vehicle_id = 'TEST-VEH'"
            ).fetchone()
            assert moving == 4
            assert idling == 7
            raise Rollback
    except Rollback:
        pass
