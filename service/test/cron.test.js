import { createExecutionContext, createScheduledController, env, waitOnExecutionContext } from 'cloudflare:test';
import { beforeEach, describe, expect, it } from 'vitest';
import { expireLocks, purgeDue } from '../src/cron.js';
import worker from '../src/index.js';
import { NOW, clearDb, getCell, insertCells, insertSheet, setCell } from './fixtures.js';
import { minimalPng } from './png.js';

const OLD = '2026-11-14T12:00:00Z';
const FRESH = '2026-11-17T06:00:00Z';

beforeEach(async () => {
  await clearDb();
  await insertSheet();
  await insertCells('goteborg-norr', 4, 3);
  const list = await env.INKORG.list();
  await Promise.all(list.objects.map((o) => env.INKORG.delete(o.key)));
});

describe('expireLocks', () => {
  it('frees locks past their expiry and leaves fresh ones', async () => {
    await setCell('goteborg-norr', 0, 0, { state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: OLD, expires_at: '2026-11-16T12:00:00Z' });
    await setCell('goteborg-norr', 1, 0, { state: 'last', owner_id: 7, owner_login: 'ebba', locked_at: FRESH, expires_at: '2026-11-19T06:00:00Z' });
    expect(await expireLocks(env.DB, NOW)).toBe(1);
    expect(await getCell('goteborg-norr', 0, 0)).toMatchObject({ state: 'fri', owner_id: null, owner_login: null, expires_at: null, updated_at: NOW });
    expect((await getCell('goteborg-norr', 1, 0)).state).toBe('last');
    const ev = await env.DB.prepare("SELECT * FROM events WHERE action = 'expired'").first();
    expect(ev).toMatchObject({ github_id: 4242, login: 'klang', sheet: 'goteborg-norr', col: 0, row: 0, at: NOW });
  });

  it('skips a cell whose owner uploaded', async () => {
    await setCell('goteborg-norr', 0, 0, { state: 'vantar', owner_id: 4242, owner_login: 'klang', locked_at: OLD, expires_at: '2026-11-16T12:00:00Z', uploaded_at: '2026-11-16T12:00:01Z' });
    await setCell('goteborg-norr', 1, 0, { state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: OLD, expires_at: '2026-11-16T12:00:00Z', uploaded_at: '2026-11-16T12:00:01Z' });
    expect(await expireLocks(env.DB, NOW)).toBe(0);
    expect((await getCell('goteborg-norr', 0, 0)).state).toBe('vantar');
    expect((await getCell('goteborg-norr', 1, 0)).state).toBe('last');
  });
});

describe('purgeDue', () => {
  it('deletes the objects whose purge date has come and clears old rejection dates', async () => {
    await setCell('goteborg-norr', 2, 2, { state: 'fri', rejected_at: '2026-10-01T00:00:00Z', comment: 'old' });
    await setCell('goteborg-norr', 3, 2, { state: 'fri', rejected_at: '2026-11-10T00:00:00Z', comment: 'recent' });
    const old = 'goteborg-norr/2_2.aaaaaaaaaaaaaaaa';
    const recent = 'goteborg-norr/3_2.bbbbbbbbbbbbbbbb';
    for (const key of [`${old}.png`, `${old}.json`, `${recent}.png`]) await env.INKORG.put(key, minimalPng(1024, 1024));
    await env.DB.batch([
      env.DB.prepare('INSERT INTO purge (key, due) VALUES (?, ?)').bind(`${old}.png`, '2026-10-31T00:00:00Z'),
      env.DB.prepare('INSERT INTO purge (key, due) VALUES (?, ?)').bind(`${old}.json`, '2026-10-31T00:00:00Z'),
      env.DB.prepare('INSERT INTO purge (key, due) VALUES (?, ?)').bind(`${recent}.png`, '2026-12-10T00:00:00Z'),
    ]);
    expect(await purgeDue(env.DB, env.INKORG, NOW)).toBe(1);
    expect((await env.INKORG.list()).objects.map((o) => o.key)).toEqual([`${recent}.png`]);
    expect((await env.DB.prepare('SELECT key FROM purge').all()).results.map((r) => r.key)).toEqual([`${recent}.png`]);
    expect((await getCell('goteborg-norr', 2, 2)).rejected_at).toBeNull();
    expect((await getCell('goteborg-norr', 3, 2)).rejected_at).toBe('2026-11-10T00:00:00Z');
  });

  it('leaves objects that are not on the list', async () => {
    await env.INKORG.put('goteborg-norr/1_1.cccccccccccccccc.png', minimalPng(1024, 1024));
    expect(await purgeDue(env.DB, env.INKORG, NOW)).toBe(0);
    expect((await env.INKORG.list()).objects.map((o) => o.key)).toEqual(['goteborg-norr/1_1.cccccccccccccccc.png']);
  });
});

describe('scheduled', () => {
  it('runs both and logs the counts', async () => {
    await setCell('goteborg-norr', 0, 0, { state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: OLD, expires_at: '2026-11-16T12:00:00Z' });
    const ctx = createExecutionContext();
    await worker.scheduled(createScheduledController({ cron: '0 * * * *' }), env, ctx);
    await waitOnExecutionContext(ctx);
    expect((await getCell('goteborg-norr', 0, 0)).state).toBe('fri');
    const ev = await env.DB.prepare("SELECT detail FROM events WHERE action = 'cron'").first();
    expect(ev.detail).toBe('expired=1 purged=0');
  });
});
