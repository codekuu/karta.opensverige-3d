/* The hourly cron: locks past their time go back to free, and the inbox objects of cells sent
   back more than 30 days ago are deleted. */

import { logEvent } from './cells.js';

const PURGE_DAYS = 30;

/**
 * Frees locks whose expiry has passed and whose owner never uploaded.
 * @param {D1Database} db
 * @param {string} now
 * @returns {Promise<number>} how many were freed
 */
export async function expireLocks(db, now) {
  const { results } = await db
    .prepare("SELECT sheet, col, row, owner_id, owner_login FROM cells WHERE state = 'last' AND expires_at < ? AND uploaded_at IS NULL")
    .bind(now)
    .all();
  for (const cell of results) {
    const r = await db
      .prepare(
        "UPDATE cells SET state = 'fri', owner_id = NULL, owner_login = NULL, locked_at = NULL, expires_at = NULL, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'last' AND uploaded_at IS NULL",
      )
      .bind(now, cell.sheet, cell.col, cell.row)
      .run();
    if (r.meta.changes === 1) {
      await logEvent(db, { at: now, github_id: cell.owner_id, login: cell.owner_login, action: 'expired', sheet: cell.sheet, col: cell.col, row: cell.row });
    }
  }
  return results.length;
}

/**
 * Deletes the inbox objects of cells rejected more than 30 days ago, then forgets the date.
 * @param {D1Database} db
 * @param {R2Bucket} inkorg
 * @param {string} now
 * @returns {Promise<number>} how many cells were purged
 */
export async function purgeRejected(db, inkorg, now) {
  const before = new Date(Date.parse(now) - PURGE_DAYS * 24 * 3600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  const { results } = await db.prepare("SELECT sheet, col, row FROM cells WHERE rejected_at IS NOT NULL AND rejected_at < ? AND state = 'fri'").bind(before).all();
  for (const cell of results) {
    await inkorg.delete([`${cell.sheet}/${cell.col}_${cell.row}.png`, `${cell.sheet}/${cell.col}_${cell.row}.json`]);
    await db.prepare('UPDATE cells SET rejected_at = NULL WHERE sheet = ? AND col = ? AND row = ?').bind(cell.sheet, cell.col, cell.row).run();
  }
  return results.length;
}

/**
 * The scheduled entry: both sweeps, then one log line with the counts.
 * @param {import('./db.js').Env} env
 * @param {string} now
 */
export async function runCron(env, now) {
  const expired = await expireLocks(env.DB, now);
  const purged = await purgeRejected(env.DB, env.INKORG, now);
  await logEvent(env.DB, { at: now, action: 'cron', detail: `expired=${expired} purged=${purged}` });
}
