# Runbook

Operational procedures for the FleetEventPipeline. All commands assume the repo
root and the `.venv` virtualenv (or an equivalent environment with
`requirements.txt` installed).

## Health checks

```bash
docker compose ps                                   # db healthy?
psql "$DB_DSN" -c "select * from ctl.pipeline_run order by started_at desc limit 5;"
psql "$DB_DSN" -c "select * from ctl.watermark;"
psql "$DB_DSN" -c "select check_name, passed, observed, threshold from ctl.dq_result
                   where run_id = (select run_id from ctl.pipeline_run
                                   where job_name='dq' order by started_at desc limit 1);"
```

## Replay a failed run

A run is resumable because ingestion is watermark-driven and staging writes are
idempotent (`ON CONFLICT DO NOTHING`).

1. Inspect the failure:
   ```bash
   psql "$DB_DSN" -c "select run_id, job_name, status, notes from ctl.pipeline_run
                      where status = 'failed' order by started_at desc;"
   ```
2. Re-run ingestion for the affected source. Because the watermark only advances
   on committed pages, re-running replays from the last good cursor:
   ```bash
   python -m src.ingest.job --source gps
   python -m src.ingest.job --source geofence
   ```
3. Rebuild the mart and re-check quality:
   ```bash
   python -m src.elt.run
   python -m src.dq.checks
   ```
4. Regenerate the report:
   ```bash
   python -m src.analysis.report
   ```

## Reset a watermark

Only do this to force a full backfill (e.g. after fixing a parser bug). It
causes the vendor API to be re-read from the beginning; duplicates are deduped
downstream but raw landing will grow.

```bash
# One source
psql "$DB_DSN" -c "delete from ctl.watermark where source_name = 'gps';"

# All sources
psql "$DB_DSN" -c "truncate ctl.watermark;"

# Then re-ingest
python -m src.ingest.job --source all
```

To discard all pipeline data but keep the schema (fully reproducible demo):

```bash
python -m src.common.reset
```

## Investigate a freshness failure

The `freshness` gate fails when the newest staged ping is older than
`LOOKBACK_HOURS` (default 720).

1. Confirm the newest ping and which assets are stale:
   ```sql
   SELECT max(ts) FROM stg.gps_ping;
   SELECT vehicle_id, max(ts) AS last_ping,
          round((extract(epoch FROM (now() - max(ts)))/3600)::numeric,1) AS age_h
   FROM stg.gps_ping GROUP BY vehicle_id ORDER BY age_h DESC LIMIT 10;
   ```
2. Check whether the vendor is returning data at all:
   ```bash
   curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
        "$FLEET_API_BASE/v1/fleet/gps?limit=5" | jq '.pagination'
   ```
3. Check the watermark versus the raw max:
   ```sql
   SELECT w.source_name, w.last_cursor_ts,
          max((r.payload->>'ts')::timestamptz) AS raw_max
   FROM ctl.watermark w LEFT JOIN raw.gps_ping_raw r ON true
   GROUP BY w.source_name, w.last_cursor_ts;
   ```
   If `last_cursor_ts` is ahead of `raw_max`, the last landing committed a page
   the handler did not fully process. Re-run `python -m src.ingest.job --source gps`.
4. A small number of stale assets is expected (they simply stopped reporting). If
   the *fleet* is stale, the ingestion job is likely failing — see the run log
   `reports/alerts/<run_id>.md` or `/tmp/fleet_api.log`.

The gate's `detail` column lists up to 10 stale vehicles.

## Investigate a Power BI refresh failure

1. Verify the views exist and return rows:
   ```bash
   python -m pytest tests/test_views.py -q
   ```
2. If using exported CSVs, re-export and re-upload:
   ```bash
   python scripts/export_powerbi.py
   ```
3. If using DirectQuery, confirm the mart is populated: `SELECT count(*) FROM mart.fact_gps_ping;`
4. Confirm the freshness gate passed — a stale mart will fail incremental refresh
   range checks.

## Alerts

`python -m src.dq.checks` exits non-zero on any error-severity failure and writes
`reports/alerts/<run_id>.md` plus a GitHub Actions `::error::` annotation. Set
`ALERT_WEBHOOK` to POST failures to a Teams/Slack/Power Automate sink.
