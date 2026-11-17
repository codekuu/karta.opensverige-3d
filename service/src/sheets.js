/* Sheets and the grid: what the build page and the CLI read. */

import { error, json } from './db.js';

const GRID_CACHE = { 'Cache-Control': 'public, max-age=60' };

/**
 * Every sheet with how many cells are live (done) and how many count at all (total:
 * everything but ute), in queue order.
 * @param {D1Database} db
 * @returns {Promise<Array<import('./db.js').Sheet & { done: number, total: number }>>}
 */
export async function listSheets(db) {
  const { results } = await db
    .prepare(
      `SELECT s.*,
         (SELECT COUNT(*) FROM cells c WHERE c.sheet = s.id AND c.state = 'live') AS done,
         (SELECT COUNT(*) FROM cells c WHERE c.sheet = s.id AND c.state != 'ute') AS total
       FROM sheets s ORDER BY s.queue_order, s.id`,
    )
    .all();
  return results;
}

/**
 * The sheet and all its cells as compact rows [col, row, state, owner_login, updated_at].
 * @param {D1Database} db
 * @param {string} sheetId
 * @returns {Promise<{ sheet: import('./db.js').Sheet, cells: Array<[number, number, string, string|null, string]> } | null>}
 */
export async function getGrid(db, sheetId) {
  const sheet = await db.prepare('SELECT * FROM sheets WHERE id = ?').bind(sheetId).first();
  if (!sheet) return null;
  const { results } = await db
    .prepare('SELECT col, row, state, owner_login, updated_at FROM cells WHERE sheet = ? ORDER BY row, col')
    .bind(sheetId)
    .all();
  return { sheet, cells: results.map((c) => [c.col, c.row, c.state, c.owner_login, c.updated_at]) };
}

/** GET /v1/sheets */
export async function sheetsRoute(request, env) {
  return json(200, { sheets: await listSheets(env.DB) }, GRID_CACHE);
}

/** GET /v1/cities: sheets grouped by city, first sheet's queue order first. */
export async function citiesRoute(request, env) {
  const sheets = await listSheets(env.DB);
  /** @type {Map<string, { id: string, name: string, sheets: object[], done: number, total: number }>} */
  const cities = new Map();
  for (const s of sheets) {
    const city = cities.get(s.city) || { id: s.city, name: s.name, sheets: [], done: 0, total: 0 };
    city.sheets.push(s);
    city.done += s.done;
    city.total += s.total;
    cities.set(s.city, city);
  }
  return json(200, { cities: [...cities.values()] }, GRID_CACHE);
}

/** GET /v1/grid/:sheet */
export async function gridRoute(request, env, ctx, params) {
  const grid = await getGrid(env.DB, params.sheet);
  if (!grid) return error(404, 'no_sheet', `No sheet named ${params.sheet}.`);
  return json(200, grid, GRID_CACHE);
}
