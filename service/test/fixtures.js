/* Test fixtures: a sheet, its cells, a user, and a clean database. */

import { createExecutionContext, env, waitOnExecutionContext } from 'cloudflare:test';
import worker from '../src/index.js';

export const NOW = '2026-11-17T12:00:00Z';

/** Göteborg norr as it lies in karta.json, shrunk to a few cells for tests. */
export const SHEET = {
  id: 'goteborg-norr',
  city: 'goteborg',
  name: 'Göteborg',
  cols: 4,
  rows: 3,
  origin_lat: 57.707,
  origin_lon: 11.967,
  x_meters: -16384,
  y_meters: 13312,
  ground_height: 40,
  dzi_levels: 18,
  pipeline: '2026.11-a',
  state: 'open',
  queue_order: 1,
};

export async function clearDb() {
  await env.DB.batch([
    env.DB.prepare('DELETE FROM events'),
    env.DB.prepare('DELETE FROM cells'),
    env.DB.prepare('DELETE FROM users'),
    env.DB.prepare('DELETE FROM sheets'),
    env.DB.prepare('DELETE FROM purge'),
  ]);
  const keys = await env.GH.list();
  await Promise.all(keys.keys.map((k) => env.GH.delete(k.name)));
}

/**
 * Inserts a sheet: open and hand-made unless the overrides say otherwise. Returns the row as inserted.
 * @param {Partial<typeof SHEET> & { kind?: string, made?: string }} overrides
 */
export async function insertSheet(overrides = {}) {
  const s = { ...SHEET, kind: 'open', made: 'hand', ...overrides };
  await env.DB.prepare(
    'INSERT INTO sheets (id, city, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, pipeline, state, queue_order, rev, kind, made) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)',
  )
    .bind(s.id, s.city, s.name, s.cols, s.rows, s.origin_lat, s.origin_lon, s.x_meters, s.y_meters, s.ground_height, s.dzi_levels, s.pipeline, s.state, s.queue_order, s.kind, s.made)
    .run();
  return s;
}

/**
 * Inserts cols × rows cells. maskFn(col, row) returns the state for a cell ('fri' by default).
 * @param {string} sheet
 * @param {number} cols
 * @param {number} rows
 * @param {(col: number, row: number) => string} [maskFn]
 */
export async function insertCells(sheet, cols, rows, maskFn = () => 'fri') {
  const statements = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      statements.push(
        env.DB.prepare('INSERT INTO cells (sheet, col, row, state, updated_at) VALUES (?, ?, ?, ?, ?)').bind(sheet, col, row, maskFn(col, row), NOW),
      );
    }
  }
  await env.DB.batch(statements);
}

/** Sets one cell's columns directly. */
export async function setCell(sheet, col, row, fields) {
  const keys = Object.keys(fields);
  const sets = keys.map((k) => `${k} = ?`).join(', ');
  await env.DB.prepare(`UPDATE cells SET ${sets} WHERE sheet = ? AND col = ? AND row = ?`)
    .bind(...keys.map((k) => fields[k]), sheet, col, row)
    .run();
}

/** Inserts one cell row. */
export async function insertCell(sheet, col, row, state = 'fri') {
  await env.DB.prepare('INSERT INTO cells (sheet, col, row, state, updated_at) VALUES (?, ?, ?, ?, ?)').bind(sheet, col, row, state, NOW).run();
}

/** How many sheet rows and cell rows there are: to show that a read wrote nothing. */
export async function rowCounts() {
  const sheets = await env.DB.prepare('SELECT COUNT(*) AS n FROM sheets').first();
  const cells = await env.DB.prepare('SELECT COUNT(*) AS n FROM cells').first();
  return { sheets: sheets.n, cells: cells.n };
}

export async function getCell(sheet, col, row) {
  return env.DB.prepare('SELECT * FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
}

/** Inserts a user row. */
export async function insertUser(fields = {}) {
  const u = { github_id: 4242, login: 'klang', first_seen: NOW, approved_count: 0, trusted: 0, blocked: 0, ...fields };
  await env.DB.prepare('INSERT INTO users (github_id, login, first_seen, approved_count, trusted, blocked) VALUES (?, ?, ?, ?, ?, ?)')
    .bind(u.github_id, u.login, u.first_seen, u.approved_count, u.trusted, u.blocked)
    .run();
  return u;
}

const allow = { limit: async () => ({ success: true }) };

/**
 * Calls the worker with env overrides. GitHub is not involved: the caller stubs fetch.
 * @param {Request} request
 * @param {object} overrides
 */
export async function call(request, overrides = {}) {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, { ...env, TAKT: allow, ...overrides }, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}
