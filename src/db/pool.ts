import { Pool } from "pg";

/**
 * Lazy connection pool: it is NOT created when this module is
 * imported, only on first real use. This lets the server boot (and
 * /api/health respond) even without DATABASE_URL configured yet —
 * only the specific operation that actually needs the database fails.
 */
let _pool: Pool | null = null;

function createPool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error(
      "DATABASE_URL is not set. Copy .env.example to .env and configure it " +
        "(Supabase in production, or the local docker-compose database in development)."
    );
  }
  return new Pool({
    connectionString,
    ssl: connectionString.includes("localhost") ? false : { rejectUnauthorized: false },
  });
}

/** Proxy: every access to a `pool` method creates the real Pool only if it doesn't exist yet. */
export const pool: Pool = new Proxy({} as Pool, {
  get(_target, prop, receiver) {
    if (!_pool) _pool = createPool();
    return Reflect.get(_pool, prop, receiver);
  },
});
