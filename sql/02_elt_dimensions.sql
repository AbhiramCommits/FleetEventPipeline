-- 02_elt_dimensions.sql
-- Rebuilds the dimension tables from staging. Truncate + reload with RESTART
-- IDENTITY keeps surrogate keys deterministic across runs. Executed by
-- src/elt/run.py inside one transaction (and harmlessly by migrate, when
-- staging is empty).

CREATE OR REPLACE FUNCTION mart.haversine_km(
    lat1 double precision, lon1 double precision,
    lat2 double precision, lon2 double precision
) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN lat1 IS NULL OR lon1 IS NULL OR lat2 IS NULL OR lon2 IS NULL THEN 0
        ELSE 6371.0 * 2 * asin(sqrt(
            power(sin(radians(lat2 - lat1) / 2), 2)
            + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)
        ))
    END;
$$;

TRUNCATE mart.dim_vehicle, mart.dim_operator, mart.dim_geofence, mart.dim_date, mart.dim_time_of_day
    RESTART IDENTITY;

INSERT INTO mart.dim_vehicle (vehicle_id, name, make, model, model_year, asset_type, serial, home_yard)
SELECT vehicle_id, name, make, model, model_year, asset_type, serial, home_yard
FROM stg.vehicle
WHERE vehicle_id IS NOT NULL
ORDER BY vehicle_id;

INSERT INTO mart.dim_operator (operator_id, name, employee_no, home_yard, license_class)
SELECT operator_id, name, employee_no, home_yard, license_class
FROM stg.operator
WHERE operator_id IS NOT NULL
ORDER BY operator_id;

INSERT INTO mart.dim_geofence (geofence_id, site_name, job_number, site_type, min_lat, min_lon, max_lat, max_lon)
SELECT geofence_id, site_name, job_number, site_type, min_lat, min_lon, max_lat, max_lon
FROM stg.geofence
WHERE geofence_id IS NOT NULL
ORDER BY geofence_id;

-- Calendar dimension spanning the observed telemetry window (min one year).
WITH bounds AS (
    SELECT COALESCE(min(ts)::date, CURRENT_DATE - 365) AS d0,
           COALESCE(max(ts)::date, CURRENT_DATE) AS d1
    FROM stg.gps_ping
)
INSERT INTO mart.dim_date (date_key, full_date, year, quarter, month, month_name, day,
                           day_of_week, day_name, week_of_year, is_weekend)
SELECT to_char(d, 'YYYYMMDD')::int, d, EXTRACT(year FROM d)::int, EXTRACT(quarter FROM d)::int,
       EXTRACT(month FROM d)::int, to_char(d, 'Mon'), EXTRACT(day FROM d)::int,
       EXTRACT(isodow FROM d)::int, to_char(d, 'Dy'), EXTRACT(week FROM d)::int,
       EXTRACT(isodow FROM d)::int >= 6
FROM bounds, generate_series(bounds.d0, bounds.d1, interval '1 day') AS d;

INSERT INTO mart.dim_time_of_day (time_key, hour, hour_label, time_bucket)
SELECT h.hour, h.hour, lpad(h.hour::text, 2, '0') || ':00',
       CASE
           WHEN h.hour < 6 THEN 'Overnight'
           WHEN h.hour < 12 THEN 'Morning'
           WHEN h.hour < 17 THEN 'Afternoon'
           ELSE 'Evening'
       END
FROM generate_series(0, 23) AS h(hour);
