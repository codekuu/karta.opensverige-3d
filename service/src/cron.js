/* The hourly cron: locks past their time go back to free, and the inbox objects of uploads
   rejected more than 30 days ago are deleted from the purge list. */

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
 * Deletes the inbox objects whose purge date has come, then forgets them. A rejected upload
 * goes on this list with its own key, so a newer upload of the same cell is never touched.
 * Rejection dates older than 30 days are cleared at the same time.
 * @param {D1Database} db
 * @param {R2Bucket} inkorg
 * @param {string} now
 * @returns {Promise<number>} how many uploads were deleted (two objects each)
 */
export async function purgeDue(db, inkorg, now) {
  const { results } = await db.prepare('SELECT key FROM purge WHERE due <= ? ORDER BY due LIMIT 500').bind(now).all();
  const keys = results.map((r) => r.key);
  if (keys.length) {
    await inkorg.delete(keys);
    await db.batch(keys.map((key) => db.prepare('DELETE FROM purge WHERE key = ?').bind(key)));
  }
  const before = new Date(Date.parse(now) - PURGE_DAYS * 24 * 3600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  await db.prepare("UPDATE cells SET rejected_at = NULL WHERE rejected_at IS NOT NULL AND rejected_at < ? AND state = 'fri'").bind(before).run();
  return keys.length / 2;
}

/**
 * The scheduled entry: both sweeps, then one log line with the counts.
 * @param {import('./db.js').Env} env
 * @param {string} now
 */
export async function runCron(env, now) {
  const expired = await expireLocks(env.DB, now);
  const purged = await purgeDue(env.DB, env.INKORG, now);
  await logEvent(env.DB, { at: now, action: 'cron', detail: `expired=${expired} purged=${purged}` });
}
