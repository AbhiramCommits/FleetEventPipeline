# Power BI dashboard specification

Three report pages. Every visual lists the exact field/measure it binds to.

## Page 1 — Fleet Overview

Audience: operations managers. Question: "How is the fleet being used today?"

| Visual | Type | Fields / measures |
| --- | --- | --- |
| Utilization by asset type | Clustered bar | Axis `vw_fleet_utilization[asset_type]`; Values `[Utilization %]`, `[Idle %]` |
| Active assets over time | Line | Axis `dim_date[full_date]`; Values `[Active Assets]` |
| Idle hours trend | Line + column combo | Axis `dim_date[full_date]`; Columns `[Idle Hours]`, Line `[Idle Hours 7-Day Rolling]` |
| Distance travelled | Card | `[Distance (km)]` |
| Engine hours | Card | `[Engine Hours]` |
| Top idle assets | Table | `dim_vehicle[vehicle_id]`, `dim_vehicle[asset_type]`, `[Idle Hours]`, `[Idle %]` |
| Utilization MTD | Card | `[Utilization % MTD]` |

Slicers: `dim_vehicle[asset_type]`, `dim_vehicle[home_yard]`,
`dim_date[full_date]` (between), `dim_time_of_day[time_bucket]`.

Drill-through: from asset type to a per-vehicle detail page.

## Page 2 — Site Activity (geofence)

Audience: site superintendents. Question: "Who is on my job site, and how long
do they stay?"

| Visual | Type | Fields / measures |
| --- | --- | --- |
| Entries vs exits by site | Clustered column | Axis `vw_geofence_dwell_by_site[site_name]`; Values `[Geofence Entries]`, `[Geofence Exits]` |
| Mean dwell by site | Bar | Axis `vw_geofence_dwell_by_site[site_name]`; Values `[Avg Dwell (min)]` |
| Dwell distribution | Histogram (binned) | `fact_geofence_transition[dwell_min]` |
| Site map | Map / Azure Maps | Latitude/longitude from `fact_gps_ping`; Legend `dim_vehicle[asset_type]` |
| Unmatched exits | Card + table | `vw_geofence_dwell_by_site[unmatched_exits]` (data-quality signal) |
| Asset visits matrix | Matrix | Rows `dim_geofence[site_name]`; Columns `dim_vehicle[asset_type]`; Values `[Geofence Entries]` |

Slicers: `dim_geofence[site_name]`, `dim_geofence[job_number]`,
`dim_geofence[site_type]`, `dim_date[full_date]`.

## Page 3 — Pipeline Health

Audience: data platform engineers. Question: "Did the pipeline run and is the
data trustworthy?"

| Visual | Type | Fields / measures |
| --- | --- | --- |
| DQ gate status | Table | `vw_data_quality_scorecard[check_name]`, `[status]`, `[observed]`, `[threshold]`, `[detail]` |
| Gate pass rate | Gauge / Card | `[DQ Gates Passed]` out of 7 |
| Data freshness | Card | `[Data Freshness (hours)]` |
| Rejected pings % | Card | `[Pings Rejected %]` |
| Reject reasons | Donut | `ctl.reject[reason_code]` (via a thin import query) |
| Runs over time | Line | `ctl.pipeline_run[started_at]` by `status` |
| Watermark lag | Card | newest `ctl.watermark[last_cursor_ts]` vs `now()` |

Slicers: `vw_data_quality_scorecard[severity]`, `vw_data_quality_scorecard[passed]`.

Conditional formatting: red for `status = "FAIL"`, amber when
`observed > 0.8 * threshold`.
