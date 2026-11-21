/* Claim and release: the lock on a cell. The lock is one conditional UPDATE, so two people
   claiming the same cell in the same instant get one winner and one 'taken'. */

import { requireUser } from './auth.js';
import { error, json } from './db.js';

export const LOCK_HOURS = 48;

/**
 * How many cells a user may hold locked at once.
 * @param {import('./db.js').User} user
 * @returns {number}
 */
export function lockLimit(user) {
  return user.trusted ? 8 : 1;
}

/**
 * Writes one line to the event log.
 * @param {D1Database} db
 * @param {{ at: string, github_id: number|null, login: string|null, action: string, sheet?: string|null, col?: number|null, row?: number|null, detail?: string|null }} e
 */
export async function logEvent(db, e) {
  await db
    .prepare('INSERT INTO events (at, github_id, login, action, sheet, col, row, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
    .bind(e.at, e.github_id ?? null, e.login ?? null, e.action, e.sheet ?? null, e.col ?? null, e.row ?? null, e.detail ?? null)
    .run();
}

/**
 * ISO time `hours` after `now`.
 * @param {string} now
 * @param {number} hours
 * @returns {string}
 */
function plusHours(now, hours) {
  return new Date(Date.parse(now) + hours * 3600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/**
 * Locks a free cell for the user.
 * @param {D1Database} db
 * @param {import('./db.js').User} user
 * @param {string} sheet
 * @param {number} col
 * @param {number} row
 * @param {string} now
 * @returns {Promise<{ ok: true, expires_at: string } | { ok: false, status: number, code: string }>}
 */
export async function claimCell(db, user, sheet, col, row, now) {
  const sheetRow = await db.prepare('SELECT id, state FROM sheets WHERE id = ?').bind(sheet).first();
  if (!sheetRow) return { ok: false, status: 404, code: 'no_sheet' };
  if (sheetRow.state !== 'open') return { ok: false, status: 403, code: 'sheet_closed' };
  const cell = await db.prepare('SELECT state FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
  if (!cell) return { ok: false, status: 404, code: 'no_cell' };
  if (cell.state === 'ute') return { ok: false, status: 422, code: 'outside' };
  const limit = lockLimit(user);
  const held = await db.prepare("SELECT COUNT(*) AS n FROM cells WHERE owner_id = ? AND state = 'last'").bind(user.github_id).first();
  if (held.n >= limit) return { ok: false, status: 403, code: 'too_many_locks' };
  const expires_at = plusHours(now, LOCK_HOURS);
  // The lock and the limit are decided by this one statement, so parallel claims cannot
  // slip past the count read above.
  const result = await db
    .prepare(
      "UPDATE cells SET state = 'last', owner_id = ?, owner_login = ?, locked_at = ?, expires_at = ?, rejected_at = NULL, comment = NULL, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'fri' AND (SELECT COUNT(*) FROM cells WHERE owner_id = ? AND state = 'last') < ?",
    )
    .bind(user.github_id, user.login, now, expires_at, now, sheet, col, row, user.github_id, limit)
    .run();
  if (result.meta.changes !== 1) {
    const after = await db.prepare('SELECT state FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
    if (after && after.state === 'fri') return { ok: false, status: 403, code: 'too_many_locks' };
    return { ok: false, status: 409, code: 'taken' };
  }
  await logEvent(db, { at: now, github_id: user.github_id, login: user.login, action: 'claim', sheet, col, row });
  return { ok: true, expires_at };
}

/**
 * Gives an unfinished lock back.
 * @param {D1Database} db
 * @param {import('./db.js').User} user
 * @param {string} sheet
 * @param {number} col
 * @param {number} row
 * @param {string} now
 * @returns {Promise<{ ok: true } | { ok: false, status: number, code: string }>}
 */
export async function releaseCell(db, user, sheet, col, row, now) {
  const cell = await db.prepare('SELECT state, owner_id, uploaded_at FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
  if (!cell) return { ok: false, status: 404, code: 'no_cell' };
  if (cell.owner_id !== user.github_id) return { ok: false, status: 403, code: 'not_yours' };
  if (cell.state !== 'last' || cell.uploaded_at) return { ok: false, status: 409, code: 'already_uploaded' };
  await db
    .prepare(
      "UPDATE cells SET state = 'fri', owner_id = NULL, owner_login = NULL, locked_at = NULL, expires_at = NULL, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'last' AND owner_id = ?",
    )
    .bind(now, sheet, col, row, user.github_id)
    .run();
  await logEvent(db, { at: now, github_id: user.github_id, login: user.login, action: 'release', sheet, col, row });
  return { ok: true };
}

const MESSAGES = {
  no_sheet: 'No such sheet.',
  no_cell: 'No such cell.',
  sheet_closed: 'That sheet is not open for claims.',
  outside: 'That cell lies outside the city and is not drawn.',
  too_many_locks: 'You already hold as many cells as you may. Finish or release one first.',
  taken: 'Someone else holds that cell. Pick another.',
  not_yours: 'That cell is not yours.',
  already_uploaded: 'That cell is already uploaded and cannot be released.',
};

/**
 * Reads { sheet, col, row } from a JSON body.
 * @param {Request} request
 * @returns {Promise<{ sheet: string, col: number, row: number } | null>}
 */
export async function readCellBody(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return null;
  }
  if (!body || typeof body.sheet !== 'string' || !/^[a-z0-9-]+$/.test(body.sheet)) return null;
  if (!Number.isInteger(body.col) || !Number.isInteger(body.row) || body.col < 0 || body.row < 0) return null;
  return { sheet: body.sheet, col: body.col, row: body.row };
}

/** POST /v1/claim */
export const claimRoute = requireUser(async (request, env, ctx, params, now, user) => {
  const body = await readCellBody(request);
  if (!body) return error(400, 'bad_body', 'Send JSON with sheet, col and row (integers from 0).');
  const r = await claimCell(env.DB, user, body.sheet, body.col, body.row, now);
  if (!r.ok) return error(r.status, r.code, MESSAGES[r.code]);
  return json(201, { ...body, expires_at: r.expires_at });
});

/** POST /v1/release */
export const releaseRoute = requireUser(async (request, env, ctx, params, now, user) => {
  const body = await readCellBody(request);
  if (!body) return error(400, 'bad_body', 'Send JSON with sheet, col and row (integers from 0).');
  const r = await releaseCell(env.DB, user, body.sheet, body.col, body.row, now);
  if (!r.ok) return error(r.status, r.code, MESSAGES[r.code]);
  return json(200, { ok: true });
});
