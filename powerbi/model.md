# Power BI semantic model

## Star schema

```mermaid
erDiagram
    dim_vehicle ||--o{ fact_gps_ping : vehicle_key
    dim_operator ||--o{ fact_gps_ping : operator_key
    dim_geofence ||--o{ fact_gps_ping : geofence_key
    dim_date ||--o{ fact_gps_ping : date_key
    dim_time_of_day ||--o{ fact_gps_ping : time_key
    dim_vehicle ||--o{ fact_trip_segment : vehicle_key
    dim_vehicle ||--o{ fact_idle_event : vehicle_key
    dim_vehicle ||--o{ fact_geofence_transition : vehicle_key
    dim_geofence ||--o{ fact_geofence_transition : geofence_key
    dim_vehicle {
        int vehicle_key PK
        text vehicle_id
        text asset_type
        text home_yard
    }
    dim_operator {
        int operator_key PK
        text operator_id
        text home_yard
    }
    dim_geofence {
        int geofence_key PK
        text geofence_id
        text site_name
        text job_number
        text site_type
    }
    dim_date {
        int date_key PK
        date full_date
        int year
        int month
    }
    dim_time_of_day {
        int time_key PK
        int hour
        text time_bucket
    }
    fact_gps_ping {
        bigint ping_key PK
        int vehicle_key FK
        int date_key FK
        timestamptz event_ts
        double distance_km
        boolean is_moving
        boolean is_idling
    }
    fact_trip_segment {
        bigint trip_key PK
        int vehicle_key FK
        timestamptz start_ts
        timestamptz end_ts
        double distance_km
    }
    fact_idle_event {
        bigint idle_key PK
        int vehicle_key FK
        timestamptz start_ts
        double duration_min
    }
    fact_geofence_transition {
        bigint transition_key PK
        int vehicle_key FK
        int geofence_key FK
        timestamptz entered_at
        timestamptz exited_at
        double dwell_min
        boolean unmatched_flag
    }
```

## Relationships and cardinality

| From | To | Cardinality | Cross-filter |
| --- | --- | --- | --- |
| fact_gps_ping[vehicle_key] | dim_vehicle[vehicle_key] | many-to-one | single |
| fact_gps_ping[operator_key] | dim_operator[operator_key] | many-to-one | single |
| fact_gps_ping[geofence_key] | dim_geofence[geofence_key] | many-to-one | single |
| fact_gps_ping[date_key] | dim_date[date_key] | many-to-one | single |
| fact_gps_ping[time_key] | dim_time_of_day[time_key] | many-to-one | single |
| fact_trip_segment[vehicle_key] | dim_vehicle[vehicle_key] | many-to-one | single |
| fact_idle_event[vehicle_key] | dim_vehicle[vehicle_key] | many-to-one | single |
| fact_geofence_transition[vehicle_key] | dim_vehicle[vehicle_key] | many-to-one | single |
| fact_geofence_transition[geofence_key] | dim_geofence[geofence_key] | many-to-one | single |

`geofence_key` and `operator_key` are nullable in `fact_gps_ping` (pings outside
any geofence / with no operator) and relate as many-to-one with blank rows
allowed.

## Storage mode

- **Import** for the dimensions and the `vw_*` aggregate views (fast visuals).
- **Dual** for `dim_date` / `dim_time_of_day` (used by both Import visuals and
  DirectQuery measures).
- **DirectQuery** is supported for `fact_gps_ping` if the org prefers live data;
  switch the partition's storage mode in Tabular Editor. The views are written
  to be sargable for DirectQuery.

## Incremental refresh

Configure an incremental-refresh policy on the large fact tables using the
Power BI `RangeStart` / `RangeEnd` parameters mapped to `dim_date[full_date]`:

- Store 3 years, refresh 30 days.
- Parameterized partition filter:
  `fact_gps_ping[event_ts] >= RangeStart and fact_gps_ping[event_ts] < RangeEnd`.
- Detect data changes on a `modified`/`event_ts` column; schedule the refresh
  after the ELT/DQ job completes (GitHub Actions can trigger it via the Power BI
  REST API).

## Row-level security (RLS)

Create roles on `dim_vehicle` keyed by `home_yard`:

```dax
[home_yard] = "YARD-01"
```

Assign each yard superuser to their yard's role; add an `ALL` role mapped to
corporate. RLS filters propagate to the facts through the star-schema
relationships.
