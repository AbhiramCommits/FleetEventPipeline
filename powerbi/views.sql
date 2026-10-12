-- powerbi/views.sql
-- Power BI semantic layer. These views are what the Power BI dataset imports
-- (Import or DirectQuery). Applied last by src/common/migrate.py.

-- Daily per-asset activity with utilization and idle split.
CREATE OR REPLACE VIEW mart.vw_equipment_activity_daily AS
WITH trips AS (
    SELECT vehicle_key, to_char(start_ts, 'YYYYMMDD')::int AS date_key,
           sum(extract(epoch FROM (end_ts - start_ts)) / 60.0) AS moving_minutes,
           sum(distance_km) AS distance_km
    FROM mart.fact_trip_segment
    GROUP BY vehicle_key, to_char(start_ts, 'YYYYMMDD')::int
),
idle AS (
    SELECT vehicle_key, to_char(start_ts, 'YYYYMMDD')::int AS date_key,
           sum(duration_min) AS idle_minutes
    FROM mart.fact_idle_event
    GROUP BY vehicle_key, to_char(start_ts, 'YYYYMMDD')::int
)
SELECT
    dd.full_date,
    dv.vehicle_id,
    dv.name,
    dv.asset_type,
    dv.home_yard,
    COALESCE(t.moving_minutes, 0) AS moving_minutes,
    COALESCE(i.idle_minutes, 0) AS idle_minutes,
    COALESCE(t.moving_minutes, 0) + COALESCE(i.idle_minutes, 0) AS engine_on_minutes,
    COALESCE(t.distance_km, 0) AS distance_km,
    CASE WHEN COALESCE(t.moving_minutes, 0) + COALESCE(i.idle_minutes, 0) > 0
         THEN round((COALESCE(t.moving_minutes, 0)
               / (COALESCE(t.moving_minutes, 0) + COALESCE(i.idle_minutes, 0)) * 100)::numeric, 2)
         ELSE 0 END AS utilization_pct
FROM mart.dim_vehicle dv
JOIN mart.dim_date dd ON true
LEFT JOIN trips t ON t.vehicle_key = dv.vehicle_key AND t.date_key = dd.date_key
LEFT JOIN idle  i ON i.vehicle_key = dv.vehicle_key AND i.date_key = dd.date_key
WHERE t.vehicle_key IS NOT NULL OR i.vehicle_key IS NOT NULL;

-- Idle summary per asset.
CREATE OR REPLACE VIEW mart.vw_idle_summary AS
SELECT
    dv.vehicle_id,
    dv.name,
    dv.asset_type,
    dv.home_yard,
    round((sum(f.duration_min) / 60.0)::numeric, 2) AS idle_hours,
    count(*) AS idle_events,
    round(avg(f.duration_min)::numeric, 2) AS avg_idle_min,
    round(max(f.duration_min)::numeric, 2) AS max_idle_min
FROM mart.fact_idle_event f
JOIN mart.dim_vehicle dv USING (vehicle_key)
GROUP BY dv.vehicle_id, dv.name, dv.asset_type, dv.home_yard;

-- Geofence dwell by site.
CREATE OR REPLACE VIEW mart.vw_geofence_dwell_by_site AS
SELECT
    dg.site_name,
    dg.job_number,
    dg.site_type,
    count(*) FILTER (WHERE f.entered_at IS NOT NULL) AS matched_visits,
    count(*) FILTER (WHERE f.entered_at IS NULL) AS unmatched_exits,
    round(avg(f.dwell_min)::numeric, 2) AS mean_dwell_min,
    round((sum(f.dwell_min) / 60.0)::numeric, 2) AS total_dwell_hours
FROM mart.fact_geofence_transition f
JOIN mart.dim_geofence dg USING (geofence_key)
GROUP BY dg.site_name, dg.job_number, dg.site_type;

-- Fleet utilization rolled up by asset type and home yard.
CREATE OR REPLACE VIEW mart.vw_fleet_utilization AS
WITH trips AS (
    SELECT dv.asset_type, dv.home_yard,
           sum(extract(epoch FROM (f.end_ts - f.start_ts)) / 60.0) AS moving_minutes,
           sum(f.distance_km) AS distance_km
    FROM mart.fact_trip_segment f JOIN mart.dim_vehicle dv USING (vehicle_key)
    GROUP BY dv.asset_type, dv.home_yard
),
idle AS (
    SELECT dv.asset_type, dv.home_yard, sum(f.duration_min) AS idle_minutes
    FROM mart.fact_idle_event f JOIN mart.dim_vehicle dv USING (vehicle_key)
    GROUP BY dv.asset_type, dv.home_yard
)
SELECT
    COALESCE(t.asset_type, i.asset_type) AS asset_type,
    COALESCE(t.home_yard, i.home_yard) AS home_yard,
    round(COALESCE(t.moving_minutes, 0)::numeric, 2) AS moving_minutes,
    round(COALESCE(i.idle_minutes, 0)::numeric, 2) AS idle_minutes,
    round(COALESCE(t.distance_km, 0)::numeric, 2) AS distance_km,
    round((COALESCE(t.moving_minutes, 0)
          / NULLIF(COALESCE(t.moving_minutes, 0) + COALESCE(i.idle_minutes, 0), 0) * 100)::numeric, 2) AS utilization_pct,
    round((COALESCE(i.idle_minutes, 0)
          / NULLIF(COALESCE(t.moving_minutes, 0) + COALESCE(i.idle_minutes, 0), 0) * 100)::numeric, 2) AS idle_pct
FROM trips t
FULL OUTER JOIN idle i ON i.asset_type = t.asset_type AND i.home_yard = t.home_yard;

-- Data-quality scorecard for the most recent DQ run.
CREATE OR REPLACE VIEW mart.vw_data_quality_scorecard AS
SELECT
    dr.check_name,
    dr.severity,
    dr.passed,
    CASE WHEN dr.passed THEN 'PASS' ELSE 'FAIL' END AS status,
    dr.observed,
    dr.threshold,
    dr.detail,
    pr.started_at AS run_started_at
FROM ctl.dq_result dr
JOIN ctl.pipeline_run pr ON pr.run_id = dr.run_id
WHERE dr.run_id = (
    SELECT run_id FROM ctl.pipeline_run
    WHERE job_name = 'dq' ORDER BY started_at DESC LIMIT 1
);
