'use strict';

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const { pool } = require('../src/db');
const { processEvents } = require('../src/server');
const { validationResponse, unknownEventTypes } = require('../src/validation');
const { normalizeGpsPing, mphToKph } = require('../src/normalize');
const { writeGpsRows } = require('../src/writer');

function gpsRow(id) {
  return {
    id,
    vehicleId: 'VEH-001',
    operatorId: 'OP-001',
    ts: new Date().toISOString(),
    lat: 39.1,
    lon: -94.5,
    speedMph: 30,
    heading: 45,
    engineOn: true,
    odometerMi: 1234.5,
    engineHours: 42.0,
    fuelLevelPct: 55.0,
  };
}

function event(rows, runId) {
  return {
    id: crypto.randomUUID(),
    eventType: 'Fleet.GpsPingBatchLanded',
    subject: '/fleet/vehicles/VEH-001',
    eventTime: new Date().toISOString(),
    dataVersion: '1.0',
    data: { source: 'gps', vehicleId: 'VEH-001', runId, count: rows.length, rows },
  };
}

async function dbAvailable() {
  try {
    await pool.query('SELECT 1');
    return true;
  } catch {
    return false;
  }
}

test('Event Grid subscription validation handshake echoes the code', async () => {
  const events = [
    {
      eventType: 'Microsoft.EventGrid.SubscriptionValidationEvent',
      data: { validationCode: 'abc-123' },
    },
  ];
  assert.strictEqual(validationResponse(events).validationResponse, 'abc-123');

  const result = await processEvents(events, pool);
  assert.strictEqual(result.status, 200);
  assert.strictEqual(result.body.validationResponse, 'abc-123');
});

test('unknown event types are rejected with 400', async () => {
  const events = [{ eventType: 'Fleet.SomethingElse', data: { rows: [] } }];
  assert.deepStrictEqual(unknownEventTypes(events), ['Fleet.SomethingElse']);
  const result = await processEvents(events, pool);
  assert.strictEqual(result.status, 400);
});

test('mph -> kph unit conversion is correct', () => {
  assert.strictEqual(mphToKph(60), 96.561);
  const { row } = normalizeGpsPing(gpsRow('u1'));
  assert.strictEqual(row.speed_kph, 48.28);
  assert.strictEqual(row.speed_mph, 30);
});

test('normalization classifies defect payloads with reason codes', () => {
  const now = Date.now();
  assert.strictEqual(normalizeGpsPing({ ...gpsRow('d1'), lat: null, lon: null }, now).reasonCode, 'MISSING_GPS');
  assert.strictEqual(normalizeGpsPing({ ...gpsRow('d2'), lat: 0, lon: 0 }, now).reasonCode, 'NULL_ISLAND');
  assert.strictEqual(normalizeGpsPing({ ...gpsRow('d3'), lat: 999, lon: 500 }, now).reasonCode, 'OUT_OF_RANGE_LATLON');
  const old = new Date(now - 40 * 24 * 3600 * 1000).toISOString();
  assert.strictEqual(normalizeGpsPing({ ...gpsRow('d4'), ts: old }, now).reasonCode, 'LATE_ARRIVING');
});

test('duplicate event posted twice writes exactly one row', async (t) => {
  if (!(await dbAvailable())) return t.skip('Postgres unavailable');
  const id = `test-dup-${crypto.randomUUID()}`;
  try {
    const first = await processEvents(event([gpsRow(id)], null), pool);
    const second = await processEvents(event([gpsRow(id)], null), pool);
    assert.strictEqual(first.body.accepted, 1);
    assert.strictEqual(second.body.accepted, 0);
    assert.strictEqual(second.body.deduped, 1);
    const { rows } = await pool.query('SELECT count(*)::int AS n FROM stg.gps_ping WHERE vendor_event_id = $1', [id]);
    assert.strictEqual(rows[0].n, 1);
  } finally {
    await pool.query('DELETE FROM stg.gps_ping WHERE vendor_event_id = $1', [id]);
  }
});

test('missing-GPS payload lands in ctl.reject', async (t) => {
  if (!(await dbAvailable())) return t.skip('Postgres unavailable');
  const runId = crypto.randomUUID();
  const bad = { ...gpsRow(`test-rej-${runId}`), lat: null, lon: null };
  try {
    const result = await processEvents(event([bad], runId), pool);
    assert.strictEqual(result.body.rejected, 1);
    const { rows } = await pool.query(
      "SELECT count(*)::int AS n FROM ctl.reject WHERE run_id = $1 AND reason_code = 'MISSING_GPS'",
      [runId]
    );
    assert.strictEqual(rows[0].n, 1);
  } finally {
    await pool.query('DELETE FROM ctl.reject WHERE run_id = $1', [runId]);
  }
});

test('writeGpsRows reports dedupe counts for repeated rows', async (t) => {
  if (!(await dbAvailable())) return t.skip('Postgres unavailable');
  const id = `test-batch-${crypto.randomUUID()}`;
  const row = normalizeGpsPing(gpsRow(id)).row;
  try {
    const a = await writeGpsRows(pool, [row, row], null);
    assert.strictEqual(a.inserted, 1);
    assert.strictEqual(a.deduped, 1);
  } finally {
    await pool.query('DELETE FROM stg.gps_ping WHERE vendor_event_id = $1', [id]);
  }
});
