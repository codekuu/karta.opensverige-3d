import { env } from 'cloudflare:test';
import { beforeEach, describe, expect, it } from 'vitest';
import { getGrid, listSheets, openSheet } from '../src/sheets.js';
import { NOW, SHEET, call, clearDb, insertCells, insertSheet, setCell } from './fixtures.js';

beforeEach(clearDb);

describe('listSheets', () => {
  it('counts done and total, leaving ute out', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3, (col, row) => (col === 3 && row === 2 ? 'ute' : 'fri'));
    await setCell('goteborg-norr', 0, 0, { state: 'live' });
    await setCell('goteborg-norr', 1, 0, { state: 'live' });
    await setCell('goteborg-norr', 2, 0, { state: 'vantar' });
    const sheets = await listSheets(env.DB);
    expect(sheets).toHaveLength(1);
    expect(sheets[0]).toMatchObject({ id: 'goteborg-norr', done: 2, total: 11, state: 'open' });
  });
});

describe('getGrid', () => {
  it('returns compact rows', async () => {
    await insertSheet({ cols: 2, rows: 1 });
    await insertCells('goteborg-norr', 2, 1);
    await setCell('goteborg-norr', 1, 0, { state: 'last', owner_login: 'klang', owner_id: 4242 });
    const grid = await getGrid(env.DB, 'goteborg-norr');
    expect(grid.sheet.id).toBe('goteborg-norr');
    expect(grid.cells).toEqual([
      [0, 0, 'fri', null, NOW],
      [1, 0, 'last', 'klang', NOW],
    ]);
  });

  it('is null for an unknown sheet', async () => {
    expect(await getGrid(env.DB, 'nope')).toBeNull();
  });
});

describe('routes', () => {
  it('grid 404s on an unknown sheet and carries a 60 s cache header when found', async () => {
    const missing = await call(new Request('https://x/v1/grid/nope'));
    expect(missing.status).toBe(404);
    expect((await missing.json()).error).toBe('no_sheet');
    await insertSheet({ cols: 1, rows: 1 });
    await insertCells('goteborg-norr', 1, 1);
    const found = await call(new Request('https://x/v1/grid/goteborg-norr'));
    expect(found.status).toBe(200);
    expect(found.headers.get('Cache-Control')).toBe('public, max-age=60');
    expect((await found.json()).cells).toEqual([[0, 0, 'fri', null, NOW]]);
  });

  it('cities group sheets by city with summed counts', async () => {
    await insertSheet({ id: 'stockholm-norr', city: 'stockholm', name: 'Stockholm', cols: 1, rows: 1, state: 'done', queue_order: 0 });
    await insertCells('stockholm-norr', 1, 1, () => 'live');
    await insertSheet({ id: 'stockholm-soder', city: 'stockholm', name: 'Stockholm', cols: 2, rows: 1, state: 'queued', queue_order: 9 });
    await insertCells('stockholm-soder', 2, 1);
    await insertSheet({ cols: 1, rows: 1 });
    await insertCells('goteborg-norr', 1, 1);
    const r = await call(new Request('https://x/v1/cities'));
    expect(r.status).toBe(200);
    const { cities } = await r.json();
    expect(cities.map((c) => c.id)).toEqual(['stockholm', 'goteborg']);
    expect(cities[0]).toMatchObject({ id: 'stockholm', name: 'Stockholm', done: 1, total: 3 });
    expect(cities[0].sheets.map((s) => s.id)).toEqual(['stockholm-norr', 'stockholm-soder']);
    const list = await call(new Request('https://x/v1/sheets'));
    expect((await list.json()).sheets).toHaveLength(3);
  });
});

describe('the source of a sheet', () => {
  const body = { ...SHEET, cols: 2, rows: 1, mask: [[0, 0]] };

  it('is open and hand-made unless said otherwise, and the grid shows both', async () => {
    await insertSheet({ cols: 1, rows: 1 });
    const r = await call(new Request('https://x/v1/grid/goteborg-norr'));
    expect((await r.json()).sheet).toMatchObject({ id: 'goteborg-norr', kind: 'open', made: 'hand' });
  });

  it('the migration marks stockholm-norr google and leaves goteborg-norr open', async () => {
    await insertSheet({ id: 'stockholm-norr', city: 'stockholm', name: 'Stockholm', queue_order: 0 });
    await insertSheet();
    // The migration ran on an empty table at setup. Its data statements are run again here, on rows.
    const migration = env.TEST_MIGRATIONS.find((m) => m.name.startsWith('0004'));
    const updates = migration.queries.filter((q) => /UPDATE sheets/i.test(q));
    expect(updates).toHaveLength(1);
    for (const q of updates) await env.DB.prepare(q).run();
    const kinds = await env.DB.prepare('SELECT id, kind FROM sheets ORDER BY id').all();
    expect(kinds.results).toEqual([
      { id: 'goteborg-norr', kind: 'open' },
      { id: 'stockholm-norr', kind: 'google' },
    ]);
  });

  it('openSheet stores the kind it is given', async () => {
    expect((await openSheet(env.DB, { ...body, kind: 'google' }, NOW)).ok).toBe(true);
    const row = await env.DB.prepare("SELECT kind, made FROM sheets WHERE id = 'goteborg-norr'").first();
    expect(row).toEqual({ kind: 'google', made: 'hand' });
  });

  it('openSheet without a kind makes an open sheet', async () => {
    expect((await openSheet(env.DB, body, NOW)).ok).toBe(true);
    expect((await env.DB.prepare("SELECT kind FROM sheets WHERE id = 'goteborg-norr'").first()).kind).toBe('open');
  });

  it('openSheet refuses a kind that is not a source and writes nothing', async () => {
    expect(await openSheet(env.DB, { ...body, kind: 'x' }, NOW)).toMatchObject({ ok: false, status: 400, code: 'bad_body', field: 'kind' });
    expect((await env.DB.prepare('SELECT COUNT(*) AS n FROM sheets').first()).n).toBe(0);
  });

  it('the table refuses a kind or a maker outside the two words', async () => {
    await insertSheet();
    await expect(env.DB.prepare("UPDATE sheets SET kind = 'bing'").run()).rejects.toThrow(/CHECK/);
    await expect(env.DB.prepare("UPDATE sheets SET made = 'magic'").run()).rejects.toThrow(/CHECK/);
  });
});
