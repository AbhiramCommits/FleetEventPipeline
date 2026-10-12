'use strict';

/**
 * Persistence for the Event Grid handler.
 *
 * Writes normalized rows into stg.gps_ping / stg.geofence_event with
 * `ON CONFLICT (vendor_event_id) DO NOTHING` for idempotent de-duplication,
 * routes invalid payloads to ctl.reject, and maintains per-run counters on
 * ctl.pipeline_run.
 */

const GPS_COLUMNS = [
  'vendor_event_id',
  'vehicle_id',
  'operator_id',
  'ts',
  'lat',
  'lon',
  'speed_mph',
  'speed_kph',
  'heading_deg',
  'engine_on',
  'odometer_mi',
  'engine_hours',
  'fuel_level_pct',
  'run_id',
];

const GEOFENCE_COLUMNS = [
  'vendor_event_id',
  'vehicle_id',
  'geofence_id',
  'event_type',
  'ts',
  'lat',
  'lon',
  'run_id',
];

const BATCH_SIZE = 500;

function chunks(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function buildInsert(table, columns, rows, runId) {
  const params = [];
  const tuples = [];
  for (const row of rows) {
    const placeholders = columns.map((col) => {
      params.push(col === 'run_id' ? runId : row[col] === undefined ? null : row[col]);
      return `$${params.length}`;
    });
    tuples.push(`(${placeholders.join(', ')})`);
  }
  const sql = `INSERT INTO ${table} (${columns.join(', ')}) VALUES ${tuples.join(', ')} ON CONFLICT (vendor_event_id) DO NOTHING`;
  return { sql, params };
}

async function bumpRun(pool, runId, { deduped = 0, rejected = 0 } = {}) {
  if (!runId) return;
  await pool.query(
    `UPDATE ctl.pipeline_run
        SET rows_deduped  = rows_deduped  + $2,
            rows_rejected = rows_rejected + $3
      WHERE run_id = $1`,
    [runId, deduped, rejected]
  );
}

async function writeRows(pool, table, columns, rows, runId) {
  if (!rows.length) return { inserted: 0, deduped: 0 };
  let inserted = 0;
  for (const batch of chunks(rows, BATCH_SIZE)) {
    const { sql, params } = buildInsert(table, columns, batch, runId);
    const res = await pool.query(sql, params);
    inserted += res.rowCount;
  }
  const deduped = rows.length - inserted;
  await bumpRun(pool, runId, { deduped });
  return { inserted, deduped };
}

/** Insert normalized GPS rows; returns {inserted, deduped}. */
function writeGpsRows(pool, rows, runId) {
  return writeRows(pool, 'stg.gps_ping', GPS_COLUMNS, rows, runId);
}

/** Insert normalized geofence rows; returns {inserted, deduped}. */
function writeGeofenceRows(pool, rows, runId) {
  return writeRows(pool, 'stg.geofence_event', GEOFENCE_COLUMNS, rows, runId);
}

/** Route an invalid payload to ctl.reject. */
async function writeReject(pool, sourceName, reasonCode, payload, runId) {
  await pool.query(
    'INSERT INTO ctl.reject (source_name, reason_code, payload, run_id) VALUES ($1, $2, $3, $4)',
    [sourceName, reasonCode, JSON.stringify(payload), runId || null]
  );
  await bumpRun(pool, runId, { rejected: 1 });
}

module.exports = { writeGpsRows, writeGeofenceRows, writeReject, bumpRun, BATCH_SIZE };
