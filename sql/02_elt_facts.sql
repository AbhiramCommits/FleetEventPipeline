-- 02_elt_facts.sql
-- Derives the fact tables from staging using SQL window functions only.
-- Truncate + reload with RESTART IDENTITY keeps the mart deterministic.

TRUNCATE mart.fact_gps_ping, mart.fact_trip_segment, mart.fact_idle_event, mart.fact_geofence_transition
    RESTART IDENTITY;

-- ---------------------------------------------------------------------------
-- fact_gps_ping : one row per deduplicated ping, with point-to-point distance
-- ---------------------------------------------------------------------------
WITH base AS (
    SELECT p.vendor_event_id, p.vehicle_id, p.operator_id, p.ts, p.lat, p.lon,
           COALESCE(p.speed_kph, p.speed_mph * 1.609344, 0) AS speed_kph,
           p.engine_on, p.odometer_mi, p.engine_hours
    FROM stg.gps_ping p
    WHERE p.lat IS NOT NULL AND p.lon IS NOT NULL AND p.ts IS NOT NULL
),
seq AS (
    SELECT b.*,
           LAG(lat) OVER w AS prev_lat,
           LAG(lon) OVER w AS prev_lon,
           LAG(ts)  OVER w AS prev_ts
    FROM base b
    WINDOW w AS (PARTITION BY vehicle_id ORDER BY ts, vendor_event_id)
)
INSERT INTO mart.fact_gps_ping (
    vendor_event_id, vehicle_key, operator_key, geofence_key, date_key, time_key,
    event_ts, lat, lon, speed_kph, engine_on, odometer_mi, engine_hours,
    distance_km, is_moving, is_idling)
SELECT s.vendor_event_id, dv.vehicle_key, dop.operator_key, gf.geofence_key,
       to_char(s.ts, 'YYYYMMDD')::int,
       EXTRACT(hour FROM s.ts)::int,
       s.ts, s.lat, s.lon, round(s.speed_kph::numeric, 3), s.engine_on, s.odometer_mi, s.engine_hours,
       round((CASE WHEN s.prev_ts IS NULL THEN 0
                   ELSE mart.haversine_km(s.prev_lat, s.prev_lon, s.lat, s.lon) END)::numeric, 4),
       (s.speed_kph > 2),
       (COALESCE(s.engine_on, false) AND s.speed_kph < 2)
FROM seq s
JOIN mart.dim_vehicle dv ON dv.vehicle_id = s.vehicle_id
LEFT JOIN mart.dim_operator dop ON dop.operator_id = s.operator_id
LEFT JOIN LATERAL (
    SELECT g.geofence_key
    FROM mart.dim_geofence g
    WHERE s.lat BETWEEN g.min_lat AND g.max_lat
      AND s.lon BETWEEN g.min_lon AND g.max_lon
    LIMIT 1
) gf ON true;

-- ---------------------------------------------------------------------------
-- fact_trip_segment : continuous moving segment per vehicle (gaps and islands)
-- ---------------------------------------------------------------------------
WITH pings AS (
    SELECT p.vehicle_id, p.operator_id, p.ts,
           COALESCE(p.speed_kph, p.speed_mph * 1.609344, 0) AS speed_kph,
           p.lat, p.lon,
           LAG(p.lat) OVER w AS prev_lat,
           LAG(p.lon) OVER w AS prev_lon,
           LAG(p.ts)  OVER w AS prev_ts,
           (COALESCE(p.speed_kph, p.speed_mph * 1.609344, 0) > 2) AS moving,
           gf.geofence_key
    FROM stg.gps_ping p
    LEFT JOIN LATERAL (
        SELECT g.geofence_key FROM mart.dim_geofence g
        WHERE p.lat BETWEEN g.min_lat AND g.max_lat AND p.lon BETWEEN g.min_lon AND g.max_lon
        LIMIT 1
    ) gf ON true
    WHERE p.lat IS NOT NULL AND p.lon IS NOT NULL AND p.ts IS NOT NULL
    WINDOW w AS (PARTITION BY p.vehicle_id ORDER BY p.ts, p.vendor_event_id)
),
marked AS (
    SELECT *,
           CASE WHEN moving AND (prev_ts IS NULL OR moving IS DISTINCT FROM
                LAG(moving) OVER (PARTITION BY vehicle_id ORDER BY ts, lat, lon)) THEN 1 ELSE 0 END AS is_start
    FROM pings
),
grouped AS (
    SELECT *, SUM(is_start) OVER (PARTITION BY vehicle_id ORDER BY ts, lat, lon) AS seg
    FROM marked
)
INSERT INTO mart.fact_trip_segment (
    vehicle_key, operator_key, start_ts, end_ts, distance_km, avg_speed_kph, max_speed_kph,
    start_geofence_key, end_geofence_key, ping_count)
SELECT dv.vehicle_key, dop.operator_key, g.start_ts, g.end_ts, g.distance_km, g.avg_speed, g.max_speed,
       g.start_geofence, g.end_geofence, g.ping_count
FROM (
    SELECT vehicle_id, operator_id, seg,
           min(ts) AS start_ts, max(ts) AS end_ts,
           round(sum(CASE WHEN prev_ts IS NULL THEN 0
                          ELSE mart.haversine_km(prev_lat, prev_lon, lat, lon) END)::numeric, 3) AS distance_km,
           round(avg(speed_kph)::numeric, 2) AS avg_speed,
           round(max(speed_kph)::numeric, 2) AS max_speed,
           (array_agg(geofence_key ORDER BY ts))[1] AS start_geofence,
           (array_agg(geofence_key ORDER BY ts DESC))[1] AS end_geofence,
           count(*) AS ping_count
    FROM grouped
    WHERE moving
    GROUP BY vehicle_id, operator_id, seg
    HAVING count(*) >= 2 AND max(ts) > min(ts)
) g
JOIN mart.dim_vehicle dv ON dv.vehicle_id = g.vehicle_id
LEFT JOIN mart.dim_operator dop ON dop.operator_id = g.operator_id;

-- ---------------------------------------------------------------------------
-- fact_idle_event : contiguous engine-on + speed < 2 kph windows >= 5 minutes
-- ---------------------------------------------------------------------------
WITH pings AS (
    SELECT p.vehicle_id, p.operator_id, p.ts,
           (COALESCE(p.engine_on, false)
            AND COALESCE(p.speed_kph, p.speed_mph * 1.609344, 0) < 2) AS idling,
           gf.geofence_key
    FROM stg.gps_ping p
    LEFT JOIN LATERAL (
        SELECT g.geofence_key FROM mart.dim_geofence g
        WHERE p.lat BETWEEN g.min_lat AND g.max_lat AND p.lon BETWEEN g.min_lon AND g.max_lon
        LIMIT 1
    ) gf ON true
    WHERE p.ts IS NOT NULL
),
marked AS (
    SELECT *,
           CASE WHEN idling AND (LAG(idling) OVER (PARTITION BY vehicle_id ORDER BY ts) IS DISTINCT FROM true)
                THEN 1 ELSE 0 END AS is_start
    FROM pings
),
grouped AS (
    SELECT *, SUM(is_start) OVER (PARTITION BY vehicle_id ORDER BY ts) AS seg
    FROM marked
),
idle_segments AS (
    SELECT vehicle_id, operator_id, seg,
           min(ts) AS start_ts, max(ts) AS end_ts,
           extract(epoch FROM (max(ts) - min(ts))) / 60.0 AS duration_min,
           (array_agg(geofence_key ORDER BY ts))[1] AS geofence_key
    FROM grouped
    WHERE idling
    GROUP BY vehicle_id, operator_id, seg
    HAVING extract(epoch FROM (max(ts) - min(ts))) / 60.0 >= 5
)
INSERT INTO mart.fact_idle_event (vehicle_key, operator_key, geofence_key, start_ts, end_ts, duration_min)
SELECT dv.vehicle_key, dop.operator_key, i.geofence_key, i.start_ts, i.end_ts, round(i.duration_min::numeric, 2)
FROM idle_segments i
JOIN mart.dim_vehicle dv ON dv.vehicle_id = i.vehicle_id
LEFT JOIN mart.dim_operator dop ON dop.operator_id = i.operator_id;

-- ---------------------------------------------------------------------------
-- fact_geofence_transition : entry/exit pairs resolved into dwell time
-- ---------------------------------------------------------------------------
WITH ev AS (
    SELECT vehicle_id, geofence_id, event_type, ts,
           LEAD(event_type) OVER w AS next_type,
           LEAD(ts)         OVER w AS next_ts,
           LAG(event_type)  OVER w AS prev_type
    FROM stg.geofence_event
    WHERE ts IS NOT NULL
    WINDOW w AS (PARTITION BY vehicle_id, geofence_id ORDER BY ts, vendor_event_id)
),
resolved AS (
    -- entries: matched when the next event for the same vehicle/geofence is an exit
    SELECT vehicle_id, geofence_id, ts AS entered_at,
           CASE WHEN next_type = 'exit' THEN next_ts END AS exited_at,
           (next_type IS DISTINCT FROM 'exit') AS unmatched_flag
    FROM ev
    WHERE event_type = 'entry'
    UNION ALL
    -- exits with no preceding entry are unmatched
    SELECT vehicle_id, geofence_id, NULL::timestamptz AS entered_at, ts AS exited_at, true AS unmatched_flag
    FROM ev
    WHERE event_type = 'exit' AND prev_type IS DISTINCT FROM 'entry'
)
INSERT INTO mart.fact_geofence_transition (vehicle_key, geofence_key, entered_at, exited_at, dwell_min, unmatched_flag)
SELECT dv.vehicle_key, dg.geofence_key, r.entered_at, r.exited_at,
       CASE WHEN r.entered_at IS NOT NULL AND r.exited_at IS NOT NULL
            THEN round((extract(epoch FROM (r.exited_at - r.entered_at)) / 60.0)::numeric, 2) END,
       r.unmatched_flag
FROM resolved r
JOIN mart.dim_vehicle dv ON dv.vehicle_id = r.vehicle_id
JOIN mart.dim_geofence dg ON dg.geofence_id = r.geofence_id;
