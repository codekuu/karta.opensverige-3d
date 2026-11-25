/* GET /v1/place: which sheet and cell a point on the earth belongs to. It only reads. */

import { env } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { claimCell } from '../src/cells.js';
import { openSheet } from '../src/sheets.js';
import { nearestCell, sheetAt } from '../src/tiling.js';
import { NOW, call, clearDb, insertCell, insertSheet, rowCounts, setCell } from './fixtures.js';

// A hand-made sheet over central Stockholm. The cells named below are what cli/rutor/frame.py
// answers for this sheet and those points.
const STOCKHOLM = { id: 'stockholm-norr', city: 'stockholm', name: 'Stockholm', cols: 64, rows: 52, origin_lat: 59.33, origin_lon: 18.06, x_meters: -8192, y_meters: 6656, ground_height: 30, kind: 'google', queue_order: 0 };
const GRID = { id: 'se-n5602-e1262', city: 'se', name: 'N 56,02 E 12,62', cols: 61, rows: 47, origin_lat: 56.025, origin_lon: 12.625, x_meters: -7808, y_meters: 6016 + 68 * Math.SQRT1_2, ground_height: 68, dzi_levels: 17, kind: 'google', made: 'grid', queue_order: 100 };

function place(query) {
  return call(new Request(`https://x/v1/place${query}`));
}

beforeEach(async () => {
  await clearDb();
  // Nobody is asked anything from outside: no login, no height.
  vi.stubGlobal('fetch', async () => {
    throw new Error('place must not fetch');
  });
});

afterEach(() => vi.unstubAllGlobals());

describe('GET /v1/place', () => {
  it('answers the hand-made sheet and its real cell for a point in central Stockholm', async () => {
    await insertSheet(STOCKHOLM);
    await insertCell('stockholm-norr', 33, 25, 'fri');
    const r = await place('?lat=59.3326&lon=18.0649');
    expect(r.status).toBe(200);
    expect(await r.json()).toMatchObject({ sheet: { id: 'stockholm-norr', kind: 'google', made: 'hand', exists: true, cols: 64 }, col: 33, row: 25, state: 'fri' });
  });

  it('tells the state of the cell there', async () => {
    await insertSheet(STOCKHOLM);
    await insertCell('stockholm-norr', 34, 27, 'live');
    await insertCell('stockholm-norr', 18, 16, 'ute');
    expect(await (await place('?lat=59.3251&lon=18.0711')).json()).toMatchObject({ col: 34, row: 27, state: 'live' });
    expect(await (await place('?lat=59.36&lon=18.0')).json()).toMatchObject({ col: 18, row: 16, state: 'ute' });
  });

  it('shows a cell that is being taken off the map as locked, as the grid does', async () => {
    await insertSheet(STOCKHOLM);
    await insertCell('stockholm-norr', 33, 25, 'fri');
    await setCell('stockholm-norr', 33, 25, { removed_at: NOW });
    expect((await (await place('?lat=59.3326&lon=18.0649')).json()).state).toBe('last');
  });

  it('answers a sheet nobody has made yet for Helsingborg, with a free cell', async () => {
    const r = await place('?lat=56.0465&lon=12.6945');
    expect(r.status).toBe(200);
    const body = await r.json();
    expect(body).toMatchObject({ col: 47, row: 16, state: 'fri' });
    expect(body.sheet).toEqual({
      id: 'se-n5602-e1262',
      city: 'se',
      name: 'N 56,02 E 12,62',
      cols: 61,
      rows: 47,
      origin_lat: 56.025,
      origin_lon: 12.625,
      x_meters: -7808,
      y_meters: 6016,
      ground_height: 0,
      dzi_levels: 17,
      pipeline: env.PIPELINE_NOW,
      state: 'open',
      queue_order: 100,
      rev: 0,
      kind: 'google',
      made: 'grid',
      exists: false,
    });
  });

  it('answers the same cell once the sheet has a row and a ground height', async () => {
    await insertSheet(GRID);
    await insertCell('se-n5602-e1262', 47, 16, 'last');
    const body = await (await place('?lat=56.0465&lon=12.6945')).json();
    expect(body).toMatchObject({ sheet: { id: 'se-n5602-e1262', exists: true, ground_height: 68 }, col: 47, row: 16, state: 'last' });
    // A neighbour that nobody has taken has no row, and is free.
    expect(await (await place('?lat=56.0465&lon=12.6905')).json()).toMatchObject({ col: 46, row: 16, state: 'fri' });
  });

  it('gives every point of a box a cell, the corners too', async () => {
    for (const [lat, lon] of [[55.950001, 12.500001], [56.099999, 12.500001], [55.950001, 12.749999], [56.099999, 12.749999]]) {
      const body = await (await place(`?lat=${lat}&lon=${lon}`)).json();
      expect(body.sheet.id).toBe('se-n5602-e1262');
      expect(body.col).toBeGreaterThanOrEqual(0);
      expect(body.col).toBeLessThan(61);
      expect(body.row).toBeGreaterThanOrEqual(0);
      expect(body.row).toBeLessThan(47);
    }
  });

  it('answers a finished city with nothing free, and no claim is had there', async () => {
    // The body the README gives for Malmö, with a height.
    const malmo = { id: 'malmo-norr', city: 'malmo', name: 'Malmö', cols: 62, rows: 36, origin_lat: 55.605, origin_lon: 13, x_meters: -7680, y_meters: 3584, ground_height: 40, dzi_levels: 17, pipeline: '2026.11-a', state: 'done', queue_order: 2, kind: 'google', mask: [] };
    expect(await openSheet(env.DB, malmo, NOW)).toEqual({ ok: true, cells: 2232, fri: 0 });
    const body = await (await place('?lat=55.605&lon=13.0')).json();
    expect(body).toMatchObject({ sheet: { id: 'malmo-norr', state: 'done', kind: 'google', made: 'hand', exists: true }, state: 'ute' });
    const klang = { github_id: 4242, login: 'klang', first_seen: NOW, approved_count: 0, trusted: 0, blocked: 0 };
    expect(await claimCell(env.DB, klang, 'malmo-norr', body.col, body.row, NOW, env)).toMatchObject({ ok: false, status: 403, code: 'sheet_closed' });
    // The tiling's own sheet over the same ground is refused too.
    const tile = sheetAt(55.605, 13.0);
    const over = nearestCell(tile, 55.605, 13.0);
    expect(await claimCell(env.DB, klang, tile.id, over.col, over.row, NOW, env, 'google')).toMatchObject({ ok: false, status: 409, code: 'other_sheet' });
  });

  it('is 404 outside for Oslo', async () => {
    const r = await place('?lat=59.9139&lon=10.7522');
    expect(r.status).toBe(404);
    expect((await r.json()).error).toBe('outside');
  });

  it('is 400 for what is not a point', async () => {
    for (const query of ['', '?lat=56', '?lat=x&lon=12', '?lat=56.0&lon=', '?lat=1e2&lon=12', '?lat=56.0&lon=12.0&lat=57', '?lat=91&lon=12', '?lat=56&lon=181', '?lat=0x38&lon=12']) {
      const r = await place(query);
      expect(r.status, query).toBe(400);
      expect((await r.json()).error).toBe('bad_query');
    }
  });

  it('writes nothing, whatever it answers', async () => {
    await insertSheet(STOCKHOLM);
    const before = await rowCounts();
    await place('?lat=59.3326&lon=18.0649');
    await place('?lat=56.0465&lon=12.6945');
    await place('?lat=59.9139&lon=10.7522');
    expect(await rowCounts()).toEqual(before);
    expect((await env.DB.prepare('SELECT COUNT(*) AS n FROM events').first()).n).toBe(0);
  });

  it('may be kept for 60 seconds, and read from the build page', async () => {
    const r = await call(new Request('https://x/v1/place?lat=56.0465&lon=12.6945', { headers: { Origin: 'https://karta.opensverige.se' } }));
    expect(r.headers.get('Cache-Control')).toBe('public, max-age=60');
    expect(r.headers.get('Access-Control-Allow-Origin')).toBe('https://karta.opensverige.se');
  });

  it('is not mixed up with another point by the cache in memory', async () => {
    const overrides = { PUBLIC_CACHE_SECONDS: '60' };
    const a = await call(new Request('https://x/v1/place?lat=56.0465&lon=12.6945'), overrides);
    const b = await call(new Request('https://x/v1/place?lat=57.7&lon=14.2'), overrides);
    expect((await a.json()).sheet.id).toBe('se-n5602-e1262');
    expect((await b.json()).sheet.id).not.toBe('se-n5602-e1262');
  });
});
