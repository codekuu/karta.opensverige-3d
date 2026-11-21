import { env } from 'cloudflare:test';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { openSheet, seedCells } from '../src/sheets.js';
import { NOW, SHEET, call, clearDb, getCell, insertCells, insertSheet, insertUser } from './fixtures.js';

const body = { ...SHEET, cols: 3, rows: 2, mask: [[0, 0], [1, 0], [2, 0], [0, 1]] };

beforeEach(clearDb);

describe('openSheet', () => {
  it('writes fri cells for the mask and ute for the rest', async () => {
    const r = await openSheet(env.DB, body, NOW);
    expect(r).toEqual({ ok: true, cells: 6, fri: 4 });
    const sheet = await env.DB.prepare('SELECT * FROM sheets WHERE id = ?').bind('goteborg-norr').first();
    expect(sheet).toMatchObject({ city: 'goteborg', cols: 3, rows: 2, pipeline: '2026.11-a', state: 'open', rev: 0 });
    expect((await getCell('goteborg-norr', 1, 0)).state).toBe('fri');
    expect((await getCell('goteborg-norr', 1, 1)).state).toBe('ute');
    expect((await getCell('goteborg-norr', 2, 1)).state).toBe('ute');
  });

  it('is 409 the second time', async () => {
    await openSheet(env.DB, body, NOW);
    expect(await openSheet(env.DB, body, NOW)).toMatchObject({ ok: false, status: 409, code: 'exists' });
  });

  it('resumes a sheet whose cells were only half written', async () => {
    await insertSheet({ cols: 3, rows: 2 });
    await env.DB.batch([
      env.DB.prepare("INSERT INTO cells (sheet, col, row, state, updated_at) VALUES ('goteborg-norr', 0, 0, 'fri', ?)").bind(NOW),
      env.DB.prepare("INSERT INTO cells (sheet, col, row, state, updated_at) VALUES ('goteborg-norr', 1, 0, 'fri', ?)").bind(NOW),
    ]);
    const r = await openSheet(env.DB, body, NOW);
    expect(r).toEqual({ ok: true, cells: 6, fri: 4, resumed: true });
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM cells WHERE sheet = 'goteborg-norr'").first()).n).toBe(6);
    expect((await getCell('goteborg-norr', 1, 1)).state).toBe('ute');
  });

  it('refuses a bad mask or a missing field and writes nothing', async () => {
    const outside = await openSheet(env.DB, { ...body, mask: [[0, 0], [5, 0]] }, NOW);
    expect(outside).toMatchObject({ ok: false, status: 400, code: 'bad_body', field: 'mask' });
    const missing = await openSheet(env.DB, { ...body, pipeline: undefined }, NOW);
    expect(missing).toMatchObject({ ok: false, status: 400, code: 'bad_body', field: 'pipeline' });
    expect((await env.DB.prepare('SELECT COUNT(*) AS n FROM sheets').first()).n).toBe(0);
    expect((await env.DB.prepare('SELECT COUNT(*) AS n FROM cells').first()).n).toBe(0);
  });

  it('handles a full Göteborg sheet of 130 × 118 cells', async () => {
    const mask = [];
    for (let row = 0; row < 118; row += 1) for (let col = 0; col < 130; col += 1) if ((col + row) % 4 !== 0) mask.push([col, row]);
    const r = await openSheet(env.DB, { ...body, cols: 130, rows: 118, mask }, NOW);
    expect(r).toEqual({ ok: true, cells: 15340, fri: mask.length });
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM cells WHERE sheet = 'goteborg-norr'").first()).n).toBe(15340);
  });
});

describe('seedCells', () => {
  beforeEach(async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3, (col, row) => (col === 3 && row === 2 ? 'ute' : 'fri'));
  });

  it('marks listed cells live, credited to the login', async () => {
    const r = await seedCells(env.DB, 'goteborg-norr', [[0, 0], [1, 0]], 'gustaf', 1, NOW);
    expect(r).toEqual({ ok: true, live: 2 });
    expect(await getCell('goteborg-norr', 0, 0)).toMatchObject({ state: 'live', owner_login: 'gustaf', owner_id: 1, live_at: NOW, approved_at: NOW });
    expect((await getCell('goteborg-norr', 2, 0)).state).toBe('fri');
  });

  it('refuses a cell outside the mask and writes nothing', async () => {
    const r = await seedCells(env.DB, 'goteborg-norr', [[0, 0], [3, 2]], 'gustaf', 1, NOW);
    expect(r).toMatchObject({ ok: false, status: 422, code: 'seed_outside_mask', cell: '3_2' });
    expect((await getCell('goteborg-norr', 0, 0)).state).toBe('fri');
  });
});

describe('admin routes', () => {
  function asAdmin(path, payload) {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ id: 1, login: 'gustaf' }), { status: 200, headers: { 'content-type': 'application/json' } }));
    return call(new Request(`https://x/v1/${path}`, { method: 'POST', headers: { Authorization: 'Bearer t', 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }), { ADMIN_GITHUB_ID: '1' });
  }

  it('POST /v1/sheets and POST /v1/seed', async () => {
    await insertUser({ github_id: 1, login: 'gustaf' });
    const opened = await asAdmin('sheets', body);
    expect(opened.status).toBe(201);
    expect(await opened.json()).toEqual({ ok: true, cells: 6, fri: 4 });
    const seeded = await asAdmin('seed', { sheet: 'goteborg-norr', cells: [[0, 0]], login: 'gustaf', github_id: 1 });
    expect(seeded.status).toBe(200);
    expect(await seeded.json()).toEqual({ ok: true, live: 1 });
    const { results } = await env.DB.prepare("SELECT action FROM events WHERE github_id = 1 ORDER BY id").all();
    expect(results.map((r) => r.action)).toEqual(['open_sheet', 'seed']);
    vi.unstubAllGlobals();
  });
});
