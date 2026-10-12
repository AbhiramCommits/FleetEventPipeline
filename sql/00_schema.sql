-- 00_schema.sql
-- Raw landing, staging, mart and control schemas for the FleetEventPipeline.
-- Every statement is idempotent so `python -m src.common.migrate` can be re-run safely.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ctl;

-- ---------------------------------------------------------------------------
-- Control plane
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ctl.schema_migration (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ctl.watermark (
    source_name    text PRIMARY KEY,
    last_cursor_ts timestamptz,
    last_run_id    uuid,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ctl.pipeline_run (
    run_id        uuid PRIMARY KEY,
    job_name      text NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running',
    rows_read     bigint NOT NULL DEFAULT 0,
    rows_written  bigint NOT NULL DEFAULT 0,
    rows_rejected bigint NOT NULL DEFAULT 0,
    rows_deduped  bigint NOT NULL DEFAULT 0,
    notes         text
);

CREATE TABLE IF NOT EXISTS ctl.reject (
    id          bigserial PRIMARY KEY,
    source_name text NOT NULL,
    reason_code text NOT NULL,
    payload     jsonb,
    run_id      uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ctl.dq_result (
    id         bigserial PRIMARY KEY,
    run_id     uuid,
    check_name text NOT NULL,
    severity   text NOT NULL DEFAULT 'error',
    passed     boolean NOT NULL,
    observed   numeric,
    threshold  numeric,
    detail     text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Raw landing (JSONB, exactly as the vendor returned it)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.gps_ping_raw (
    id          bigserial PRIMARY KEY,
    payload     jsonb NOT NULL,
    source_id   text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    run_id      uuid
);

CREATE TABLE IF NOT EXISTS raw.geofence_event_raw (
    id          bigserial PRIMARY KEY,
    payload     jsonb NOT NULL,
    source_id   text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    run_id      uuid
);

CREATE INDEX IF NOT EXISTS ix_gps_ping_raw_run ON raw.gps_ping_raw (run_id);
CREATE INDEX IF NOT EXISTS ix_geofence_event_raw_run ON raw.geofence_event_raw (run_id);

-- ---------------------------------------------------------------------------
-- Staging (typed, deduplicated, normalized)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stg.gps_ping (
    vendor_event_id text PRIMARY KEY,
    vehicle_id      text,
    operator_id     text,
    ts              timestamptz,
    lat             double precision,
    lon             double precision,
    speed_mph       double precision,
    speed_kph       double precision,
    heading_deg     double precision,
    engine_on       boolean,
    odometer_mi     double precision,
    engine_hours    double precision,
    fuel_level_pct  double precision,
    run_id          uuid,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_gps_ping_vehicle_ts ON stg.gps_ping (vehicle_id, ts);

CREATE TABLE IF NOT EXISTS stg.geofence_event (
    vendor_event_id text PRIMARY KEY,
    vehicle_id      text,
    geofence_id     text,
    event_type      text CHECK (event_type IN ('entry', 'exit')),
    ts              timestamptz,
    lat             double precision,
    lon             double precision,
    run_id          uuid,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_geofence_event_vehicle_ts ON stg.geofence_event (vehicle_id, ts);

CREATE TABLE IF NOT EXISTS stg.vehicle (
    vehicle_id  text PRIMARY KEY,
    name        text,
    make        text,
    model       text,
    model_year  integer,
    asset_type  text,
    serial      text,
    home_yard   text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stg.operator (
    operator_id   text PRIMARY KEY,
    name          text,
    employee_no   text,
    home_yard     text,
    license_class text,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stg.geofence (
    geofence_id text PRIMARY KEY,
    site_name   text,
    job_number  text,
    site_type   text,
    min_lat     double precision,
    min_lon     double precision,
    max_lat     double precision,
    max_lon     double precision,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
