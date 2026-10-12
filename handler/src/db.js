'use strict';

/**
 * Shared PostgreSQL pool for the Event Grid handler.
 *
 * The connection string comes from DB_DSN (loaded from .env by dotenv). In
 * Azure this would be a Key Vault reference / managed identity connection.
 */

const { Pool } = require('pg');
require('dotenv').config();

const connectionString =
  process.env.DB_DSN || 'postgresql://fleet:fleet@localhost:55432/fleet';

const pool = new Pool({ connectionString, max: 10 });

module.exports = { pool };
