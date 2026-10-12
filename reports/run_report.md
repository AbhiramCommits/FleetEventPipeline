# FleetEventPipeline — run report

_Generated 2026-10-12T21:04:30.158022+00:00_

## Headline

|------------------------|----------------|
| Events ingested (read) | 165,426        |
| Rows raw-landed        | 165,426        |
| Duplicates blocked     | 3,105          |
| Rows rejected          | 4,009          |
| Ingestion wall-clock   | 23.431 s       |
| Throughput             | 7,060.1 rows/s |
| Retries (429/503)      | 5              |
| Total pipeline runtime | 37.92 s        |
| DQ gates passed        | 7/7            |

## Rows per table

### Raw

| table              |    rows |
|--------------------|---------|
| gps_ping_raw       | 162,034 |
| geofence_event_raw |   3,392 |

### Staging

| table          |    rows |
|----------------|---------|
| gps_ping       | 154,920 |
| geofence_event |   3,392 |
| vehicle        |      40 |
| operator       |      24 |
| geofence       |       9 |

### Mart

| table                    |    rows |
|--------------------------|---------|
| dim_vehicle              |      40 |
| dim_operator             |      24 |
| dim_geofence             |       9 |
| dim_date                 |      29 |
| dim_time_of_day          |      24 |
| fact_gps_ping            | 154,920 |
| fact_trip_segment        |   4,887 |
| fact_idle_event          |   4,695 |
| fact_geofence_transition |   1,720 |

## Injected vs caught defects

| defect                   |   injected |   caught | recall   |
|--------------------------|------------|----------|----------|
| duplicate_events         |       3186 |     3105 | 97.46%   |
| late_arriving            |       1577 |     1574 | 99.81%   |
| null_gps                 |        819 |      844 | 103.05%  |
| null_island              |        809 |      834 | 103.09%  |
| out_of_range             |        738 |      757 | 102.57%  |
| unmatched_geofence_exits |          8 |        8 | 100.0%   |
| stale_vehicles           |          3 |        3 | 100.0%   |

## Rejected rows by reason

| reason_code         |   rows |
|---------------------|--------|
| LATE_ARRIVING       |   1574 |
| MISSING_GPS         |    844 |
| NULL_ISLAND         |    834 |
| OUT_OF_RANGE_LATLON |    757 |

## Utilization by asset type

| asset_type   |   moving_hours |   idle_hours | utilization   | idle   |
|--------------|----------------|--------------|---------------|--------|
| dozer        |        1295.22 |       494.81 | 72.36%        | 27.64% |
| excavator    |        1328.4  |       478    | 73.54%        | 26.46% |
| haul_truck   |        1333.1  |       479.15 | 73.56%        | 26.44% |
| pickup       |        1327.43 |       488.24 | 73.11%        | 26.89% |

## Top 10 idle assets (hours)

| vehicle_id   | name     | asset_type   |   idle_hours |
|--------------|----------|--------------|--------------|
| VEH-004      | Unit 004 | pickup       |        55.85 |
| VEH-018      | Unit 018 | dozer        |        54.44 |
| VEH-023      | Unit 023 | haul_truck   |        53.07 |
| VEH-011      | Unit 011 | haul_truck   |        52.59 |
| VEH-037      | Unit 037 | excavator    |        52.41 |
| VEH-019      | Unit 019 | haul_truck   |        51.96 |
| VEH-024      | Unit 024 | pickup       |        51.72 |
| VEH-009      | Unit 009 | excavator    |        51.46 |
| VEH-038      | Unit 038 | dozer        |        51.45 |
| VEH-012      | Unit 012 | pickup       |        51.15 |

## Geofence activity by site

| site            | job    |   entries |   exits |   mean_dwell_min |
|-----------------|--------|-----------|---------|------------------|
| Fairview Site 1 | J-1000 |       146 |     147 |           486.23 |
| Summit Site 2   | J-1007 |       147 |     147 |           480.5  |
| Bluffs Site 3   | J-1014 |       146 |     147 |           472.74 |
| Cedar Site 4    | J-1021 |       147 |     147 |           482.15 |
| Summit Site 5   | J-1028 |       126 |     126 |           477.66 |
| Summit Site 6   | J-1035 |       126 |     126 |           479.71 |

## Data-quality gates

| check                    | result   |   observed |   threshold | detail                                                                                                |
|--------------------------|----------|------------|-------------|-------------------------------------------------------------------------------------------------------|
| freshness                | PASS     |     2.8821 |         720 | newest=2026-10-12T18:11:31.568000+00:00 stale_vehicles(3)=VEH-030:77.7h, VEH-016:77.1h, VEH-034:75.7h |
| duplicate_rate           | PASS     |     1.877  |           5 | deduped=3105 total=165426                                                                             |
| invalid_gps_reject_rate  | PASS     |     1.5028 |           3 | rejects=2435 total=162034                                                                             |
| referential_integrity    | PASS     |     0      |           0 | unmatched_fact_rows=0                                                                                 |
| unmatched_geofence_exits | PASS     |     0.4762 |           1 | unmatched=8 exits=1680                                                                                |
| row_count_drift          | PASS     |     6.7993 |          40 | current=154920 previous=166222                                                                        |
| watermark_monotonicity   | PASS     |     0      |           0 | all watermarks at or ahead of raw max                                                                 |
