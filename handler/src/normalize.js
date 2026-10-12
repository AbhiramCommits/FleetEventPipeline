'use strict';

/**
 * Payload normalization for fleet telemetry.
 *
 * Converts vendor camelCase payloads into snake_case staging rows, coerces
 * types, converts units (mph -> kph), validates GPS coordinates and classifies
 * anything suspicious with a ctl.reject reason code.
 *
 * Every normalize* function returns either:
 *   { ok: true, row: {...} }
 *   { ok: false, reasonCode: '...', detail: '...' }
 */

const MPH_TO_KPH = 1.609344;
const MAX_LATENESS_HOURS = Number(process.env.MAX_LATENESS_HOURS || 720);

const REASONS = {
  MALFORMED_PAYLOAD: 'MALFORMED_PAYLOAD',
  MISSING_GPS: 'MISSING_GPS',
  NULL_ISLAND: 'NULL_ISLAND',
  OUT_OF_RANGE_LATLON: 'OUT_OF_RANGE_LATLON',
  LATE_ARRIVING: 'LATE_ARRIVING',
};

function toSnake(key) {
  return String(key)
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase();
}

function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toBool(value) {
  if (value === true || value === 'true' || value === 1 || value === '1') return true;
  if (value === false || value === 'false' || value === 0 || value === '0') return false;
  return null;
}

function parseTs(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function isLate(ts, nowMs) {
  return ts.getTime() < nowMs - MAX_LATENESS_HOURS * 3600 * 1000;
}

function reject(reasonCode, detail) {
  return { ok: false, reasonCode, detail };
}

/** Human-readable unit conversion used by tests and documentation. */
function mphToKph(mph) {
  return mph === null ? null : Math.round(mph * MPH_TO_KPH * 1000) / 1000;
}

/**
 * Normalize a single raw GPS ping.
 * @param {object} raw
 * @param {number} [nowMs]
 */
function normalizeGpsPing(raw, nowMs = Date.now()) {
  if (!raw || typeof raw !== 'object') return reject(REASONS.MALFORMED_PAYLOAD, 'not an object');

  const id = raw.id || raw.vendorEventId || raw.vendor_event_id;
  const vehicleId = raw.vehicleId || raw.vehicle_id;
  const ts = parseTs(raw.ts || raw.timestamp);
  if (!id || !vehicleId || !ts) return reject(REASONS.MALFORMED_PAYLOAD, 'missing id/vehicleId/ts');

  const lat = raw.lat === undefined ? raw.latitude : raw.lat;
  const lon = raw.lon === undefined ? raw.longitude : raw.lon;
  if (lat === null || lat === undefined || lon === null || lon === undefined || lat === '' || lon === '') {
    return reject(REASONS.MISSING_GPS, 'null coordinates');
  }
  const latN = num(lat);
  const lonN = num(lon);
  if (latN === null || lonN === null) return reject(REASONS.MISSING_GPS, 'non-numeric coordinates');
  if (latN === 0 && lonN === 0) return reject(REASONS.NULL_ISLAND, 'coordinates 0,0');
  if (Math.abs(latN) > 90 || Math.abs(lonN) > 180) {
    return reject(REASONS.OUT_OF_RANGE_LATLON, `lat=${latN} lon=${lonN}`);
  }
  if (isLate(ts, nowMs)) return reject(REASONS.LATE_ARRIVING, `ts=${ts.toISOString()}`);

  const speedMph = num(raw.speedMph === undefined ? raw.speed_mph : raw.speedMph);
  return {
    ok: true,
    row: {
      vendor_event_id: String(id),
      vehicle_id: String(vehicleId),
      operator_id: raw.operatorId || raw.operator_id || null,
      ts: ts.toISOString(),
      lat: latN,
      lon: lonN,
      speed_mph: speedMph,
      speed_kph: mphToKph(speedMph),
      heading_deg: num(raw.heading === undefined ? raw.heading_deg : raw.heading),
      engine_on: toBool(raw.engineOn === undefined ? raw.engine_on : raw.engineOn),
      odometer_mi: num(raw.odometerMi === undefined ? raw.odometer_mi : raw.odometerMi),
      engine_hours: num(raw.engineHours === undefined ? raw.engine_hours : raw.engineHours),
      fuel_level_pct: num(raw.fuelLevelPct === undefined ? raw.fuel_level_pct : raw.fuelLevelPct),
    },
  };
}

/**
 * Normalize a single raw geofence event.
 * @param {object} raw
 * @param {number} [nowMs]
 */
function normalizeGeofenceEvent(raw, nowMs = Date.now()) {
  if (!raw || typeof raw !== 'object') return reject(REASONS.MALFORMED_PAYLOAD, 'not an object');

  const id = raw.id || raw.vendorEventId || raw.vendor_event_id;
  const vehicleId = raw.vehicleId || raw.vehicle_id;
  const geofenceId = raw.geofenceId || raw.geofence_id;
  const eventType = raw.eventType || raw.event_type;
  const ts = parseTs(raw.ts || raw.timestamp);
  if (!id || !vehicleId || !geofenceId || !ts) return reject(REASONS.MALFORMED_PAYLOAD, 'missing fields');
  if (eventType !== 'entry' && eventType !== 'exit') {
    return reject(REASONS.MALFORMED_PAYLOAD, `bad eventType ${eventType}`);
  }
  if (isLate(ts, nowMs)) return reject(REASONS.LATE_ARRIVING, `ts=${ts.toISOString()}`);

  return {
    ok: true,
    row: {
      vendor_event_id: String(id),
      vehicle_id: String(vehicleId),
      geofence_id: String(geofenceId),
      event_type: eventType,
      ts: ts.toISOString(),
      lat: num(raw.lat),
      lon: num(raw.lon),
    },
  };
}

module.exports = {
  REASONS,
  MPH_TO_KPH,
  MAX_LATENESS_HOURS,
  toSnake,
  mphToKph,
  normalizeGpsPing,
  normalizeGeofenceEvent,
};
