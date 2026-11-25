/* Shared shapes and small helpers every module uses. */

/**
 * @typedef {{ id: string, city: string, name: string, cols: number, rows: number,
 *   origin_lat: number, origin_lon: number, x_meters: number, y_meters: number,
 *   ground_height: number, dzi_levels: number, pipeline: string,
 *   state: 'queued'|'open'|'done', queue_order: number, rev: number,
 *   kind: 'open'|'google', made: 'hand'|'grid' }} Sheet
 */

/**
 * @typedef {{ sheet: string, col: number, row: number,
 *   state: 'ute'|'fri'|'last'|'vantar'|'godkand'|'live',
 *   owner_id: number|null, owner_login: string|null, locked_at: string|null,
 *   expires_at: string|null, uploaded_at: string|null, approved_at: string|null,
 *   live_at: string|null, rejected_at: string|null, bytes: number|null,
 *   sha256: string|null, comment: string|null, updated_at: string }} Cell
 */

/**
 * @typedef {{ github_id: number, login: string, first_seen: string,
 *   approved_count: number, trusted: 0|1, blocked: 0|1 }} User
 */

/**
 * @typedef {{ DB: D1Database, INKORG: R2Bucket, GH: KVNamespace,
 *   TAKT: { limit(options: { key: string }): Promise<{ success: boolean }> },
 *   ADMIN_GITHUB_ID: string, PIXELBYGGET_STANGD: string, ALLOWED_ORIGINS: string }} Env
 */

/**
 * The current moment as an ISO 8601 UTC string without milliseconds.
 * @param {Date} [d]
 * @returns {string}
 */
export function nowIso(d = new Date()) {
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/**
 * A JSON response.
 * @param {number} status
 * @param {object} body
 * @param {HeadersInit} [extra]
 * @returns {Response}
 */
export function json(status, body, extra = {}) {
  const headers = new Headers(extra);
  headers.set('Content-Type', 'application/json; charset=utf-8');
  return new Response(JSON.stringify(body), { status, headers });
}

/**
 * A JSON error response with the shape { error, message }.
 * @param {number} status
 * @param {string} code
 * @param {string} message
 * @param {HeadersInit} [extra]
 * @returns {Response}
 */
export function error(status, code, message, extra = {}) {
  return json(status, { error: code, message }, extra);
}
