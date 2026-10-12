"""Export the Power BI mart views to CSV so the dataset can be built without a DB.

Usage::

    python scripts/export_powerbi.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import get_settings
from src.common.db import get_conn

VIEWS = [
    "mart.vw_equipment_activity_daily",
    "mart.vw_idle_summary",
    "mart.vw_geofence_dwell_by_site",
    "mart.vw_fleet_utilization",
    "mart.vw_data_quality_scorecard",
]


def export() -> list[str]:
    """Export each view to ``powerbi/exports/<view>.csv``."""
    out_dir = get_settings().repo_root / "powerbi" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with get_conn() as conn:
        for view in VIEWS:
            name = view.split(".")[-1]
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {view}")
                columns = [d.name for d in cur.description]
                rows = cur.fetchall()
            path = out_dir / f"{name}.csv"
            with path.open("w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(columns)
                writer.writerows(rows)
            written.append(f"{name}.csv ({len(rows)} rows)")
    return written


if __name__ == "__main__":
    for line in export():
        print(line)
    sys.exit(0)
