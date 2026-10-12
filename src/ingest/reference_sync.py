"""Full-refresh synchronization of reference data into staging.

Loads vehicles, operators and geofences from the vendor API into ``stg.vehicle``,
``stg.operator`` and ``stg.geofence``. This is a full refresh (no SCD history):
each sync truncates and reloads inside a single transaction so a failed fetch
cannot leave staging half-populated.
"""

from __future__ import annotations

from typing import Any

import psycopg

from src.common.db import execute_many
from src.common.logging import get_logger, log_event
from src.ingest.client import FleetApiClient

logger = get_logger("fleet.ingest.reference")

VEHICLE_SQL = """
INSERT INTO stg.vehicle (vehicle_id, name, make, model, model_year, asset_type, serial, home_yard, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
"""

OPERATOR_SQL = """
INSERT INTO stg.operator (operator_id, name, employee_no, home_yard, license_class, updated_at)
VALUES (%s, %s, %s, %s, %s, now())
"""

GEOFENCE_SQL = """
INSERT INTO stg.geofence (geofence_id, site_name, job_number, site_type, min_lat, min_lon, max_lat, max_lon, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
"""


def sync_references(client: FleetApiClient, conn: psycopg.Connection) -> dict[str, int]:
    """Truncate and reload all reference tables; returns row counts per table."""
    vehicles = client.fetch_reference("/v1/fleet/vehicles")
    operators = client.fetch_reference("/v1/fleet/operators")
    geofences = client.fetch_reference("/v1/fleet/geofences")

    vehicle_rows: list[tuple[Any, ...]] = [
        (
            v.get("vehicleId"),
            v.get("name"),
            v.get("make"),
            v.get("model"),
            v.get("year"),
            v.get("assetType"),
            v.get("serial"),
            v.get("homeYard"),
        )
        for v in vehicles
    ]
    operator_rows = [
        (o.get("operatorId"), o.get("name"), o.get("employeeNo"), o.get("homeYard"), o.get("licenseClass"))
        for o in operators
    ]
    geofence_rows = [
        (
            g.get("geofenceId"),
            g.get("siteName"),
            g.get("jobNumber"),
            g.get("siteType"),
            g.get("minLat"),
            g.get("minLon"),
            g.get("maxLat"),
            g.get("maxLon"),
        )
        for g in geofences
    ]

    with conn.transaction():
        conn.execute("TRUNCATE stg.vehicle")
        conn.execute("TRUNCATE stg.operator")
        conn.execute("TRUNCATE stg.geofence")
        execute_many(VEHICLE_SQL, vehicle_rows, conn, batch_size=1000)
        execute_many(OPERATOR_SQL, operator_rows, conn, batch_size=1000)
        execute_many(GEOFENCE_SQL, geofence_rows, conn, batch_size=1000)

    counts = {"vehicle": len(vehicle_rows), "operator": len(operator_rows), "geofence": len(geofence_rows)}
    log_event(logger, "reference sync complete", **counts)
    return counts
