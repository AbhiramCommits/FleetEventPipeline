'use strict';

/**
 * Express application exposing the Event Grid HTTP trigger at POST /api/eventgrid.
 *
 * The same request-processing core (`processEvents`) powers the Azure Functions
 * entry point in index.js, so local express and deployed Function share one code
 * path.
 */

const express = require('express');

const { pool } = require('./db');
const {
  isValidationEvent,
  validationResponse,
  unknownEventTypes,
} = require('./validation');
const { normalizeGpsPing, normalizeGeofenceEvent } = require('./normalize');
const { writeGpsRows, writeGeofenceRows, writeReject } = require('./writer');

const GPS_EVENT = 'Fleet.GpsPingBatchLanded';
const GEOFENCE_EVENT = 'Fleet.GeofenceEventLanded';

/**
 * Process an Event Grid event array (or a single event).
 * @param {object|object[]} events
 * @param {import('pg').Pool} db
 * @returns {Promise<{status:number, body:object}>}
 */
async function processEvents(events, db) {
  const list = Array.isArray(events) ? events : [events];

  const handshake = validationResponse(list);
  if (handshake) return { status: 200, body: handshake };

  const unknown = unknownEventTypes(list);
  if (unknown.length > 0) {
    return { status: 400, body: { error: 'unknown eventType', eventTypes: unknown } };
  }

  const nowMs = Date.now();
  const gpsRows = [];
  const geoRows = [];
  let rejected = 0;
  let runId = null;

  for (const event of list) {
    const data = event.data || {};
    if (!runId && data.runId) runId = data.runId;
    const isGps = event.eventType === GPS_EVENT;
    const source = isGps ? 'gps' : 'geofence';
    for (const raw of data.rows || []) {
      const result = isGps ? normalizeGpsPing(raw, nowMs) : normalizeGeofenceEvent(raw, nowMs);
      if (result.ok) {
        (isGps ? gpsRows : geoRows).push(result.row);
      } else {
        rejected += 1;
        await writeReject(db, source, result.reasonCode, raw, runId);
      }
    }
  }

  const gps = await writeGpsRows(db, gpsRows, runId);
  const geo = await writeGeofenceRows(db, geoRows, runId);

  return {
    status: 200,
    body: {
      accepted: gps.inserted + geo.inserted,
      deduped: gps.deduped + geo.deduped,
      rejected,
      runId,
    },
  };
}

/** Build the express app. */
function createApp(db = pool) {
  const app = express();
  app.use(express.json({ limit: '50mb' }));

  app.get('/health', (_req, res) => res.json({ status: 'ok' }));

  app.post('/api/eventgrid', async (req, res) => {
    try {
      const result = await processEvents(req.body, db);
      res.status(result.status).json(result.body);
    } catch (err) {
      console.error(JSON.stringify({ level: 'ERROR', message: 'eventgrid handler failed', error: String(err) }));
      res.status(500).json({ error: 'internal error' });
    }
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT || 7099);
  createApp().listen(port, () => {
    console.log(JSON.stringify({ level: 'INFO', message: 'handler listening', port }));
  });
}

module.exports = { createApp, processEvents };
