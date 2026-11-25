/* Sheets and the grid: what the build page and the CLI read, and how the admin opens a sheet
   and seeds the cells that are already drawn. */

import { requireAdmin } from './auth.js';
import { logEvent } from './cells.js';
import { error, json } from './db.js';
import { cellAt, gridRow, owns, ownedCellAt, sheetById } from './tiling.js';

const BATCH = 500;
const SHEET_FIELDS = [
  ['id', (v) => typeof v === 'string' && /^[a-z0-9-]+$/.test(v)],
  ['city', (v) => typeof v === 'string' && /^[a-z0-9-]+$/.test(v)],
  ['name', (v) => typeof v === 'string' && v.length > 0],
  ['cols', (v) => Number.isInteger(v) && v > 0],
  ['rows', (v) => Number.isInteger(v) && v > 0],
  ['origin_lat', (v) => typeof v === 'number'],
  ['origin_lon', (v) => typeof v === 'number'],
  ['x_meters', (v) => typeof v === 'number'],
  ['y_meters', (v) => typeof v === 'number'],
  ['ground_height', (v) => typeof v === 'number'],
  ['dzi_levels', (v) => Number.isInteger(v) && v > 0],
  ['pipeline', (v) => typeof v === 'string' && v.length > 0],
  ['queue_order', (v) => Number.isInteger(v)],
  ['state', (v) => ['queued', 'open', 'done'].includes(v)],
];
export const KINDS = ['open', 'google'];

/**
 * Inserts a sheet and all its cells: fri inside the mask, ute outside. Nothing is written on
 * refusal. A sheet that exists with fewer cells than cols × rows (a run that broke half way)
 * is resumed: the missing cells are written and the answer says resumed. `kind` is the sheet's
 * source and may be left out: a sheet is then open.
 * @param {D1Database} db
 * @param {object} body
 * @param {string} now
 * @returns {Promise<{ ok: true, cells: number, fri: number, resumed?: boolean } | { ok: false, status: number, code: string, field?: string }>}
 */
export async function openSheet(db, body, now) {
  if (!body || typeof body !== 'object') return { ok: false, status: 400, code: 'bad_body', field: 'body' };
  for (const [field, valid] of SHEET_FIELDS) {
    if (!valid(body[field])) return { ok: false, status: 400, code: 'bad_body', field };
  }
  const { cols, rows } = body;
  const kind = body.kind === undefined ? 'open' : body.kind;
  if (!KINDS.includes(kind)) return { ok: false, status: 400, code: 'bad_body', field: 'kind' };
  if (!Array.isArray(body.mask)) return { ok: false, status: 400, code: 'bad_body', field: 'mask' };
  const inMask = new Set();
  for (const entry of body.mask) {
    const [col, row] = Array.isArray(entry) ? entry : [];
    if (!Number.isInteger(col) || !Number.isInteger(row) || col < 0 || row < 0 || col >= cols || row >= rows) {
      return { ok: false, status: 400, code: 'bad_body', field: 'mask' };
    }
    inMask.add(`${col}_${row}`);
  }
  const exists = await db.prepare('SELECT cols, rows FROM sheets WHERE id = ?').bind(body.id).first();
  let resumed = false;
  if (exists) {
    const have = await db.prepare('SELECT COUNT(*) AS n FROM cells WHERE sheet = ?').bind(body.id).first();
    if (have.n >= exists.cols * exists.rows) return { ok: false, status: 409, code: 'exists' };
    if (exists.cols !== cols || exists.rows !== rows) return { ok: false, status: 409, code: 'exists' };
    resumed = true;
  }
  const statements = [];
  if (!resumed) {
    statements.push(
      db
        .prepare(
          "INSERT INTO sheets (id, city, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, pipeline, state, queue_order, rev, kind, made) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'hand')",
        )
        .bind(body.id, body.city, body.name, cols, rows, body.origin_lat, body.origin_lon, body.x_meters, body.y_meters, body.ground_height, body.dzi_levels, body.pipeline, body.state, body.queue_order, kind),
    );
  }
  const insert = db.prepare('INSERT OR IGNORE INTO cells (sheet, col, row, state, updated_at) VALUES (?, ?, ?, ?, ?)');
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      statements.push(insert.bind(body.id, col, row, inMask.has(`${col}_${row}`) ? 'fri' : 'ute', now));
    }
  }
  for (let i = 0; i < statements.length; i += BATCH) {
    await db.batch(statements.slice(i, i + BATCH));
  }
  const result = { ok: true, cells: cols * rows, fri: inMask.size };
  if (resumed) result.resumed = true;
  return result;
}

/**
 * Marks cells that are already drawn as live, credited to a login. Every cell must be fri.
 * @param {D1Database} db
 * @param {string} sheet
 * @param {Array<[number, number]>} cells
 * @param {string} login
 * @param {number} githubId
 * @param {string} now
 * @returns {Promise<{ ok: true, live: number } | { ok: false, status: number, code: string, cell?: string }>}
 */
export async function seedCells(db, sheet, cells, login, githubId, now) {
  // One query for the whole list: the first listed cell that is missing or not free, if any.
  const { results: free } = await db
    .prepare("SELECT col, row FROM cells WHERE sheet = ? AND state = 'fri' AND (col, row) IN (SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?))")
    .bind(sheet, JSON.stringify(cells))
    .all();
  const ok = new Set(free.map((c) => `${c.col}_${c.row}`));
  const bad = cells.find(([col, row]) => !ok.has(`${col}_${row}`));
  if (bad) return { ok: false, status: 422, code: 'seed_outside_mask', cell: `${bad[0]}_${bad[1]}` };
  const update = db.prepare(
    "UPDATE cells SET state = 'live', owner_id = ?, owner_login = ?, approved_at = ?, live_at = ?, updated_at = ? WHERE sheet = ? AND col = ? AND row = ? AND state = 'fri'",
  );
  const statements = cells.map(([col, row]) => update.bind(githubId, login, now, now, now, sheet, col, row));
  for (let i = 0; i < statements.length; i += BATCH) {
    await db.batch(statements.slice(i, i + BATCH));
  }
  return { ok: true, live: cells.length };
}

/** POST /v1/sheets, admin. */
export const openSheetRoute = requireAdmin(async (request, env, ctx, params, now, admin) => {
  const body = await request.json().catch(() => null);
  const r = await openSheet(env.DB, body, now);
  if (!r.ok) {
    if (r.code === 'exists') return error(409, 'exists', 'A sheet with that id already exists.');
    return error(400, 'bad_body', `The field ${r.field} is missing or wrong.`);
  }
  await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: 'open_sheet', sheet: body.id, detail: `${r.fri} of ${r.cells} cells` });
  return json(201, r);
});

/** POST /v1/seed, admin. */
export const seedRoute = requireAdmin(async (request, env, ctx, params, now, admin) => {
  const body = await request.json().catch(() => null);
  const cellsOk = body && Array.isArray(body.cells) && body.cells.every((c) => Array.isArray(c) && c.length === 2 && Number.isInteger(c[0]) && Number.isInteger(c[1]));
  if (!cellsOk || typeof body.sheet !== 'string' || typeof body.login !== 'string' || !Number.isInteger(body.github_id)) {
    return error(400, 'bad_body', 'Send JSON with sheet, cells as [[col,row],...], login and github_id.');
  }
  const r = await seedCells(env.DB, body.sheet, body.cells, body.login, body.github_id, now);
  if (!r.ok) return error(r.status, r.code, `Cell ${r.cell} is not free, so nothing was seeded.`);
  await logEvent(env.DB, { at: now, github_id: admin.github_id, login: admin.login, action: 'seed', sheet: body.sheet, detail: `${r.live} cells to ${body.login}` });
  return json(200, r);
});

const GRID_CACHE = { 'Cache-Control': 'public, max-age=60' };
const NOT_KEPT = { 'Cache-Control': 'no-store' };

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
 * A live cell the admin took down is free in the table but nobody may claim it until its
 * tiles are blanked, so it is shown as locked: a client that takes "the next free cell"
 * must not be handed one that answers being_removed.
 * A sheet of the tiling that nobody has claimed on yet has no row: it is answered as the
 * tiling computes it, with no cells. A cell with no row is free there.
 * @param {D1Database} db
 * @param {string} sheetId
 * @param {string} [pipeline] the pipeline a sheet of the tiling would be made with
 * @returns {Promise<{ sheet: import('./db.js').Sheet, cells: Array<[number, number, string, string|null, string]> } | null>}
 */
export async function getGrid(db, sheetId, pipeline = '') {
  const sheet = await db.prepare('SELECT * FROM sheets WHERE id = ?').bind(sheetId).first();
  if (!sheet) {
    const tile = sheetById(sheetId);
    // exists false: this is not a row. Its height and picture top are stand-ins until the
    // first claim asks for the real height, so nothing may be drawn from it.
    return tile ? { sheet: { ...gridRow(tile, pipeline), exists: false }, cells: [] } : null;
  }
  const { results } = await db
    .prepare('SELECT col, row, state, owner_login, updated_at, removed_at FROM cells WHERE sheet = ? ORDER BY row, col')
    .bind(sheetId)
    .all();
  return { sheet, cells: results.map((c) => [c.col, c.row, c.removed_at && c.state === 'fri' ? 'last' : c.state, c.owner_login, c.updated_at]) };
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
  const grid = await getGrid(env.DB, params.sheet, env.PIPELINE_NOW);
  if (!grid) return error(404, 'no_sheet', `No sheet named ${params.sheet}.`);
  // A sheet with no row is never kept, here or on the way: the claim that makes the row
  // changes the answer, and the renderer reads it seconds later.
  return json(200, grid, grid.sheet.exists === false ? NOT_KEPT : GRID_CACHE);
}

/**
 * The state of a cell as the grid shows it. A cell with no row is free on a sheet of the
 * tiling, whose cells are made when they are first taken, and not offered on a hand-made one.
 * @param {D1Database} db
 * @param {import('./db.js').Sheet} sheet
 * @param {number} col
 * @param {number} row
 * @returns {Promise<string>}
 */
async function shownState(db, sheet, col, row) {
  const cell = await db.prepare('SELECT state, removed_at FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet.id, col, row).first();
  if (!cell) {
    if (sheet.made !== 'grid') return 'ute';
    // Ground near the line to the next box that the next sheet owns is not offered here.
    const tile = sheetById(sheet.id);
    return tile && owns(tile, col, row) ? 'fri' : 'ute';
  }
  return cell.removed_at && cell.state === 'fri' ? 'last' : cell.state;
}

/**
 * The sheet and cell a point on the earth belongs to, or null outside Sweden. A point inside
 * a hand-made sheet's picture belongs to that sheet: what is drawn is not gridded again.
 * Anywhere else the tiling decides, and the sheet may be one nobody has made yet
 * (exists false). Reads only.
 * @param {D1Database} db
 * @param {string} pipeline the pipeline a new sheet would be drawn with
 * @param {number} lat
 * @param {number} lon
 * @returns {Promise<{ sheet: import('./db.js').Sheet & { exists: boolean }, col: number, row: number, state: string } | null>}
 */
export async function placeAt(db, pipeline, lat, lon) {
  const { results: hand } = await db.prepare("SELECT * FROM sheets WHERE made = 'hand' ORDER BY queue_order, id").all();
  for (const sheet of hand) {
    const cell = cellAt(sheet, lat, lon);
    if (cell) return { sheet: { ...sheet, exists: true }, ...cell, state: await shownState(db, sheet, cell.col, cell.row) };
  }
  // Where two boxes meet, the cell is the one a claim would accept: owned by its sheet.
  const found = ownedCellAt(lat, lon);
  if (!found) return null;
  const { tile, col, row } = found;
  const stored = await db.prepare('SELECT * FROM sheets WHERE id = ?').bind(tile.id).first();
  const sheet = stored || gridRow(tile, pipeline);
  return { sheet: { ...sheet, exists: Boolean(stored) }, col, row, state: stored ? await shownState(db, sheet, col, row) : 'fri' };
}

const DEGREES = /^-?\d{1,3}(\.\d{1,9})?$/;

/**
 * One coordinate out of a query: plain decimal degrees, given once.
 * @param {URLSearchParams} query
 * @param {string} name
 * @param {number} limit
 * @returns {number | null}
 */
function degrees(query, name, limit) {
  const all = query.getAll(name);
  if (all.length !== 1 || !DEGREES.test(all[0])) return null;
  const value = Number(all[0]);
  return Math.abs(value) <= limit ? value : null;
}

/** GET /v1/place?lat=..&lon=.. */
export async function placeRoute(request, env) {
  const query = new URL(request.url).searchParams;
  const lat = degrees(query, 'lat', 90);
  const lon = degrees(query, 'lon', 180);
  if (lat === null || lon === null) return error(400, 'bad_query', 'Send lat and lon as decimal degrees, like ?lat=56.0465&lon=12.6945.');
  const place = await placeAt(env.DB, env.PIPELINE_NOW, lat, lon);
  if (!place) return error(404, 'outside', 'That point lies outside Sweden.');
  return json(200, place, GRID_CACHE);
}
