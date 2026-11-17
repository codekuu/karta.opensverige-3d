/* The admin's side: approve, reject, trust, block, the queue, and the intake reporting cells
   live. Every action is one line in the event log. */

import { requireAdmin } from './auth.js';
import { logEvent, readCellBody } from './cells.js';
import { error, json } from './db.js';

const TRUST_AT = 10;
const COMMENT_MAX = 500;

/**
 * vantar → godkand. The owner's approved_count grows and trust arrives at the tenth.
 * @param {D1Database} db
 * @param {string} sheet
 * @param {number} col
 * @param {number} row
 * @param {string} now
 * @returns {Promise<{ ok: true } | { ok: false, status: number, code: string }>}
 */
export async function approveCell(db, sheet, col, row, now) {
  const cell = await db.prepare('SELECT state, owner_id FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
  if (!cell || cell.state !== 'vantar') return { ok: false, status: 409, code: 'not_waiting' };
  await db.batch([
    db.prepare("UPDATE cells SET state = 'godkand', approved_at = ?, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'vantar'").bind(now, now, sheet, col, row),
    db.prepare('UPDATE users SET approved_count = approved_count + 1 WHERE github_id = ?').bind(cell.owner_id),
    db.prepare('UPDATE users SET trusted = 1 WHERE github_id = ? AND approved_count >= ?').bind(cell.owner_id, TRUST_AT),
  ]);
  return { ok: true };
}

/**
 * vantar, godkand or live → fri with a comment. The inbox objects go unless the cell was live:
 * then they stay so the intake can read the cell it must blank.
 * @param {D1Database} db
 * @param {R2Bucket} inkorg
 * @param {string} sheet
 * @param {number} col
 * @param {number} row
 * @param {string} comment
 * @param {string} now
 * @returns {Promise<{ ok: true } | { ok: false, status: number, code: string }>}
 */
export async function rejectCell(db, inkorg, sheet, col, row, comment, now) {
  const cell = await db.prepare('SELECT state, owner_id, owner_login FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
  if (!cell || !['vantar', 'godkand', 'live'].includes(cell.state)) return { ok: false, status: 409, code: 'nothing_to_reject' };
  const wasLive = cell.state === 'live';
  if (!wasLive) {
    await inkorg.delete([`${sheet}/${col}_${row}.png`, `${sheet}/${col}_${row}.json`]);
  }
  await db
    .prepare(
      "UPDATE cells SET state = 'fri', owner_id = NULL, owner_login = NULL, locked_at = NULL, expires_at = NULL, uploaded_at = NULL, approved_at = NULL, live_at = NULL, bytes = NULL, sha256 = NULL, rejected_at = ?, comment = ?, updated_at = ? WHERE sheet = ? AND col = ? AND row = ?",
    )
    .bind(wasLive ? null : now, comment, now, sheet, col, row)
    .run();
  await logEvent(db, {
    at: now,
    github_id: cell.owner_id,
    login: cell.owner_login,
    action: 'reject',
    sheet,
    col,
    row,
    detail: (wasLive ? 'live-removed: ' : '') + comment,
  });
  return { ok: true };
}

/**
 * @param {D1Database} db
 * @param {string} login
 * @param {0|1} trusted
 */
export async function setTrust(db, login, trusted) {
  const r = await db.prepare('UPDATE users SET trusted = ? WHERE login = ?').bind(trusted, login).run();
  if (r.meta.changes !== 1) return { ok: false, status: 404, code: 'no_user' };
  return { ok: true };
}

/**
 * @param {D1Database} db
 * @param {string} login
 * @param {0|1} blocked
 */
export async function setBlock(db, login, blocked) {
  const r = await db.prepare('UPDATE users SET blocked = ? WHERE login = ?').bind(blocked, login).run();
  if (r.meta.changes !== 1) return { ok: false, status: 404, code: 'no_user' };
  return { ok: true };
}

/**
 * Cells in a state, each with its eight neighbours as [col, row, state].
 * @param {D1Database} db
 * @param {'vantar'|'godkand'} state
 * @returns {Promise<Array<import('./db.js').Cell & { neighbours: Array<[number, number, string]> }>>}
 */
export async function listQueue(db, state) {
  const { results } = await db.prepare('SELECT * FROM cells WHERE state = ? ORDER BY uploaded_at, sheet, row, col').bind(state).all();
  const out = [];
  for (const cell of results) {
    const { results: around } = await db
      .prepare('SELECT col, row, state FROM cells WHERE sheet = ? AND col BETWEEN ? AND ? AND row BETWEEN ? AND ? AND NOT (col = ? AND row = ?)')
      .bind(cell.sheet, cell.col - 1, cell.col + 1, cell.row - 1, cell.row + 1, cell.col, cell.row)
      .all();
    out.push({ ...cell, neighbours: around.map((n) => [n.col, n.row, n.state]) });
  }
  return out;
}

/**
 * godkand → live for every listed cell, and the sheet's revision, or nothing at all.
 * @param {D1Database} db
 * @param {string} sheet
 * @param {Array<[number, number]>} cells
 * @param {number} rev
 * @param {string} now
 * @returns {Promise<{ ok: true, live: number } | { ok: false, status: number, code: string }>}
 */
export async function markLive(db, sheet, cells, rev, now) {
  for (const [col, row] of cells) {
    const cell = await db.prepare('SELECT state FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
    if (!cell || cell.state !== 'godkand') return { ok: false, status: 409, code: 'not_approved' };
  }
  const statements = cells.map(([col, row]) =>
    db.prepare("UPDATE cells SET state = 'live', live_at = ?, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'godkand'").bind(now, now, sheet, col, row),
  );
  statements.push(db.prepare('UPDATE sheets SET rev = ? WHERE id = ?').bind(rev, sheet));
  await db.batch(statements);
  return { ok: true, live: cells.length };
}

const MESSAGES = {
  not_waiting: 'That cell is not waiting for review.',
  nothing_to_reject: 'That cell has nothing uploaded to reject.',
  no_user: 'No user with that login.',
  not_approved: 'Every listed cell must be approved. Nothing was marked live.',
};

function fail(r) {
  return error(r.status, r.code, MESSAGES[r.code] || r.code);
}

/** POST /v1/approve */
export const approveRoute = requireAdmin(async (request, env, ctx, params, now, admin) => {
  const body = await readCellBody(request);
  if (!body) return error(400, 'bad_body', 'Send JSON with sheet, col and row.');
  const r = await approveCell(env.DB, body.sheet, body.col, body.row, now);
  if (!r.ok) return fail(r);
  await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: 'approve', ...body });
  return json(200, { ok: true });
});

/** POST /v1/reject */
export const rejectRoute = requireAdmin(async (request, env, ctx, params, now, admin) => {
  const raw = await request.clone().json().catch(() => null);
  const body = await readCellBody(request);
  const comment = raw && typeof raw.comment === 'string' ? raw.comment.trim() : '';
  if (!body || !comment || comment.length > COMMENT_MAX) return error(400, 'bad_body', `Send JSON with sheet, col, row and a comment of 1 to ${COMMENT_MAX} characters.`);
  const r = await rejectCell(env.DB, env.INKORG, body.sheet, body.col, body.row, comment, now);
  if (!r.ok) return fail(r);
  await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: 'reject', ...body, detail: comment });
  return json(200, { ok: true });
});

/** POST /v1/trust and POST /v1/block */
function flagRoute(field, apply) {
  return requireAdmin(async (request, env, ctx, params, now, admin) => {
    const body = await request.json().catch(() => null);
    if (!body || typeof body.login !== 'string' || ![0, 1].includes(body[field])) return error(400, 'bad_body', `Send JSON with login and ${field} as 0 or 1.`);
    const r = await apply(env.DB, body.login, body[field]);
    if (!r.ok) return fail(r);
    await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: field, detail: `${body.login}=${body[field]}` });
    return json(200, { ok: true });
  });
}
export const trustRoute = flagRoute('trusted', setTrust);
export const blockRoute = flagRoute('blocked', setBlock);

/** GET /v1/queue?state=vantar|godkand */
export const queueRoute = requireAdmin(async (request, env) => {
  const state = new URL(request.url).searchParams.get('state') || 'vantar';
  if (!['vantar', 'godkand'].includes(state)) return error(400, 'bad_state', 'state must be vantar or godkand.');
  return json(200, { state, cells: await listQueue(env.DB, state) }, { 'Cache-Control': 'private, no-store' });
});

/** POST /v1/live */
export const liveRoute = requireAdmin(async (request, env, ctx, params, now, admin) => {
  const body = await request.json().catch(() => null);
  const cellsOk = body && Array.isArray(body.cells) && body.cells.every((c) => Array.isArray(c) && c.length === 2 && Number.isInteger(c[0]) && Number.isInteger(c[1]));
  if (!cellsOk || typeof body.sheet !== 'string' || !Number.isInteger(body.rev)) return error(400, 'bad_body', 'Send JSON with sheet, cells as [[col,row],...] and rev.');
  const r = await markLive(env.DB, body.sheet, body.cells, body.rev, now);
  if (!r.ok) return fail(r);
  await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: 'live', sheet: body.sheet, detail: `${r.live} cells, rev ${body.rev}` });
  return json(200, r);
});
