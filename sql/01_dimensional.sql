-- 01_dimensional.sql
-- Star schema for equipment utilization analytics. Idempotent.
--
-- Grain summary
--   dim_vehicle            : one row per asset
--   dim_operator           : one row per operator
--   dim_geofence           : one row per job-site geofence
--   dim_date               : one row per calendar day
--   dim_time_of_day        : one row per clock hour
--   fact_gps_ping          : one row per deduplicated GPS ping
--   fact_trip_segment      : one row per continuous moving segment per vehicle
--   fact_idle_event        : one row per contiguous idle window (engine on, speed < 2 kph, >= 5 min)
--   fact_geofence_transition: one row per entry->exit pair resolved into a dwell

CREATE TABLE IF NOT EXISTS mart.dim_vehicle (
    vehicle_key serial PRIMARY KEY,
    vehicle_id  text UNIQUE NOT NULL,
    name        text,
    make        text,
    model       text,
    model_year  integer,
    asset_type  text,
    serial      text,
    home_yard   text
);

CREATE TABLE IF NOT EXISTS mart.dim_operator (
    operator_key  serial PRIMARY KEY,
    operator_id   text UNIQUE NOT NULL,
    name          text,
    employee_no   text,
    home_yard     text,
    license_class text
);

CREATE TABLE IF NOT EXISTS mart.dim_geofence (
    geofence_key serial PRIMARY KEY,
    geofence_id  text UNIQUE NOT NULL,
    site_name    text,
    job_number   text,
    site_type    text,
    min_lat      double precision,
    min_lon      double precision,
    max_lat      double precision,
    max_lon      double precision
);

CREATE TABLE IF NOT EXISTS mart.dim_date (
    date_key     integer PRIMARY KEY,
    full_date    date UNIQUE NOT NULL,
    year         integer NOT NULL,
    quarter      integer NOT NULL,
    month        integer NOT NULL,
    month_name   text NOT NULL,
    day          integer NOT NULL,
    day_of_week  integer NOT NULL,
    day_name     text NOT NULL,
    week_of_year integer NOT NULL,
    is_weekend   boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS mart.dim_time_of_day (
    time_key    integer PRIMARY KEY,
    hour        integer NOT NULL,
    hour_label  text NOT NULL,
    time_bucket text NOT NULL
);

CREATE TABLE IF NOT EXISTS mart.fact_gps_ping (
    ping_key        bigserial PRIMARY KEY,
    vendor_event_id text UNIQUE NOT NULL,
    vehicle_key     integer NOT NULL,
    operator_key    integer,
    geofence_key    integer,
    date_key        integer NOT NULL,
    time_key        integer NOT NULL,
    event_ts        timestamptz NOT NULL,
    lat             double precision,
    lon             double precision,
    speed_kph       double precision,
    engine_on       boolean,
    odometer_mi     double precision,
    engine_hours    double precision,
    distance_km     double precision,
    is_moving       boolean,
    is_idling       boolean,
    run_id          uuid
);

CREATE INDEX IF NOT EXISTS ix_fact_gps_vehicle_ts ON mart.fact_gps_ping (vehicle_key, event_ts);
CREATE INDEX IF NOT EXISTS ix_fact_gps_date ON mart.fact_gps_ping (date_key);
CREATE INDEX IF NOT EXISTS ix_fact_gps_geofence ON mart.fact_gps_ping (geofence_key);

CREATE TABLE IF NOT EXISTS mart.fact_trip_segment (
    trip_key           bigserial PRIMARY KEY,
    vehicle_key        integer NOT NULL,
    operator_key       integer,
    start_ts           timestamptz NOT NULL,
    end_ts             timestamptz NOT NULL,
    distance_km        double precision,
    avg_speed_kph      double precision,
    max_speed_kph      double precision,
    start_geofence_key integer,
    end_geofence_key   integer,
    ping_count         integer,
    run_id             uuid
);

CREATE INDEX IF NOT EXISTS ix_fact_trip_vehicle ON mart.fact_trip_segment (vehicle_key, start_ts);

CREATE TABLE IF NOT EXISTS mart.fact_idle_event (
    idle_key     bigserial PRIMARY KEY,
    vehicle_key  integer NOT NULL,
    operator_key integer,
    geofence_key integer,
    start_ts     timestamptz NOT NULL,
    end_ts       timestamptz NOT NULL,
    duration_min double precision NOT NULL,
    run_id       uuid
);

CREATE INDEX IF NOT EXISTS ix_fact_idle_vehicle ON mart.fact_idle_event (vehicle_key, start_ts);

CREATE TABLE IF NOT EXISTS mart.fact_geofence_transition (
    transition_key bigserial PRIMARY KEY,
    vehicle_key    integer NOT NULL,
    geofence_key   integer NOT NULL,
    entered_at     timestamptz,
    exited_at      timestamptz,
    dwell_min      double precision,
    unmatched_flag boolean NOT NULL DEFAULT false,
    run_id         uuid
);

CREATE INDEX IF NOT EXISTS ix_fact_transition_geofence ON mart.fact_geofence_transition (geofence_key, entered_at);
