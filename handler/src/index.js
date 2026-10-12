'use strict';

/**
 * Azure Functions entry point for the Event Grid HTTP trigger.
 *
 * Deploy as an HTTP-triggered Function whose route is `eventgrid`. The handler
 * receives the Event Grid event array in `req.body` and writes normalized rows
 * to staging. Running this file directly (node handler/src/index.js) starts the
 * same express app for local development.
 */

const { processEvents } = require('./server');
const { pool } = require('./db');

module.exports = async function eventGridFunction(context, req) {
  const events = req.body;
  try {
    const result = await processEvents(events, pool);
    context.res = { status: result.status, body: result.body, headers: { 'Content-Type': 'application/json' } };
  } catch (err) {
    context.log.error('eventgrid handler failed', err);
    context.res = { status: 500, body: { error: 'internal error' } };
  }
};

if (require.main === module) {
  const port = Number(process.env.PORT || 7099);
  require('./server').createApp().listen(port, () => {
    console.log(JSON.stringify({ level: 'INFO', message: 'handler (local bootstrap) listening', port }));
  });
}
