"""Power BI view tests: every view returns rows and has no NULL keys."""

from __future__ import annotations

import pytest

VIEW_KEYS = {
    "mart.vw_equipment_activity_daily": ["full_date", "vehicle_id", "asset_type"],
    "mart.vw_idle_summary": ["vehicle_id", "asset_type", "home_yard"],
    "mart.vw_geofence_dwell_by_site": ["site_name", "site_type"],
    "mart.vw_fleet_utilization": ["asset_type", "home_yard"],
    "mart.vw_data_quality_scorecard": ["check_name", "status"],
}


@pytest.mark.parametrize("view", list(VIEW_KEYS))
def test_view_returns_rows(conn, view: str) -> None:
    count = conn.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    assert count > 0, f"{view} returned no rows"


@pytest.mark.parametrize("view,keys", list(VIEW_KEYS.items()))
def test_view_has_no_null_keys(conn, view: str, keys: list[str]) -> None:
    nulls = conn.execute(
        "SELECT count(*) FROM " + view + " WHERE " + " OR ".join(f"{k} IS NULL" for k in keys)
    ).fetchone()[0]
    assert nulls == 0, f"{view} has {nulls} rows with NULL keys {keys}"
