'use strict';

/**
 * Event Grid envelope validation.
 *
 * Azure sends a SubscriptionValidationEvent as the very first delivery when a
 * subscription is created; the endpoint must echo back validationCode. Any
 * eventType outside the fleet allow-list is rejected with HTTP 400.
 */

const VALIDATION_EVENT_TYPE = 'Microsoft.EventGrid.SubscriptionValidationEvent';

const ALLOWED_EVENT_TYPES = new Set([
  'Fleet.GpsPingBatchLanded',
  'Fleet.GeofenceEventLanded',
]);

/** @returns {boolean} true when the event is an Event Grid validation handshake. */
function isValidationEvent(event) {
  return Boolean(event) && event.eventType === VALIDATION_EVENT_TYPE;
}

/**
 * Extract the validation response body if any event is a handshake.
 * @returns {{validationResponse: string}|null}
 */
function validationResponse(events) {
  const handshake = events.find(isValidationEvent);
  if (!handshake) return null;
  const code = handshake.data && (handshake.data.validationCode || handshake.data.validationUrl);
  return { validationResponse: String(code) };
}

/**
 * Return the set of eventTypes that are not in the allow-list.
 * @returns {string[]}
 */
function unknownEventTypes(events) {
  const unknown = new Set();
  for (const event of events) {
    if (!isValidationEvent(event) && !ALLOWED_EVENT_TYPES.has(event.eventType)) {
      unknown.add(event.eventType);
    }
  }
  return [...unknown];
}

module.exports = {
  VALIDATION_EVENT_TYPE,
  ALLOWED_EVENT_TYPES,
  isValidationEvent,
  validationResponse,
  unknownEventTypes,
};
