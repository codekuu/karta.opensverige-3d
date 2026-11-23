import { env } from 'cloudflare:test';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LOCK_HOURS, claimCell, lockLimit, releaseCell } from '../src/cells.js';
import { NOW, call, clearDb, getCell, insertCells, insertSheet, insertUser, setCell } from './fixtures.js';

const user = { github_id: 4242, login: 'klang', first_seen: NOW, approved_count: 0, trusted: 0, blocked: 0 };
const other = { github_id: 7, login: 'ebba', first_seen: NOW, approved_count: 12, trusted: 1, blocked: 0 };

beforeEach(async () => {
  await clearDb();
  await insertSheet();
  await insertCells('goteborg-norr', 4, 3, (col, row) => (col === 3 && row === 2 ? 'ute' : 'fri'));
  await insertUser(user);
  await insertUser(other);
});

describe('claimCell', () => {
  it('locks a free cell for 48 hours', async () => {
    const r = await claimCell(env.DB, user, 'goteborg-norr', 1, 1, NOW);
    expect(LOCK_HOURS).toBe(48);
    expect(r).toEqual({ ok: true, expires_at: '2026-11-19T12:00:00Z' });
    const cell = await getCell('goteborg-norr', 1, 1);
    expect(cell).toMatchObject({ state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: NOW, expires_at: '2026-11-19T12:00:00Z', updated_at: NOW });
  });

  it('is taken when the cell is not free', async () => {
    await setCell('goteborg-norr', 1, 1, { state: 'last', owner_id: 7, owner_login: 'ebba' });
    const r = await claimCell(env.DB, user, 'goteborg-norr', 1, 1, NOW);
    expect(r).toMatchObject({ ok: false, status: 409, code: 'taken' });
  });

  it('refuses a cell outside the sheet mask', async () => {
    const r = await claimCell(env.DB, user, 'goteborg-norr', 3, 2, NOW);
    expect(r).toMatchObject({ ok: false, status: 422, code: 'outside' });
  });

  it('refuses a closed sheet and an unknown sheet', async () => {
    await insertSheet({ id: 'uppsala-norr', city: 'uppsala', name: 'Uppsala', state: 'queued', queue_order: 2 });
    await insertCells('uppsala-norr', 1, 1);
    expect(await claimCell(env.DB, user, 'uppsala-norr', 0, 0, NOW)).toMatchObject({ ok: false, status: 403, code: 'sheet_closed' });
    expect(await claimCell(env.DB, user, 'nope', 0, 0, NOW)).toMatchObject({ ok: false, status: 404, code: 'no_sheet' });
  });

  it('limits locks: 1 for a new user, 8 for a trusted one', async () => {
    expect(lockLimit(user)).toBe(1);
    expect(lockLimit(other)).toBe(8);
    expect((await claimCell(env.DB, user, 'goteborg-norr', 0, 0, NOW)).ok).toBe(true);
    expect(await claimCell(env.DB, user, 'goteborg-norr', 1, 0, NOW)).toMatchObject({ ok: false, status: 403, code: 'too_many_locks' });
    const eight = [[0, 1], [1, 1], [2, 1], [3, 1], [0, 2], [1, 2], [2, 2], [1, 0]];
    for (const [col, row] of eight) {
      expect((await claimCell(env.DB, other, 'goteborg-norr', col, row, NOW)).ok).toBe(true);
    }
    expect(await claimCell(env.DB, other, 'goteborg-norr', 2, 0, NOW)).toMatchObject({ ok: false, status: 403, code: 'too_many_locks' });
  });

  it('stops a new user who already has five cells waiting for review', async () => {
    // Uploading frees the lock, so without this one account could park 20 cells a day in the queue.
    const waiting = [[0, 1], [1, 1], [2, 1], [3, 1], [0, 2]];
    for (const [col, row] of waiting) {
      await env.DB.prepare("UPDATE cells SET state = 'vantar', owner_id = ?, owner_login = ?, uploaded_at = ? WHERE sheet = 'goteborg-norr' AND col = ? AND row = ?")
        .bind(user.github_id, user.login, NOW, col, row)
        .run();
    }
    expect(await claimCell(env.DB, user, 'goteborg-norr', 0, 0, NOW)).toMatchObject({ ok: false, status: 403, code: 'too_many_waiting' });
    // One of them is reviewed: room again.
    await env.DB.prepare("UPDATE cells SET state = 'godkand' WHERE sheet = 'goteborg-norr' AND col = 0 AND row = 1").run();
    expect((await claimCell(env.DB, user, 'goteborg-norr', 0, 0, NOW)).ok).toBe(true);
  });

  it('does not hold a trusted user to the waiting limit', async () => {
    for (const [col, row] of [[0, 1], [1, 1], [2, 1], [3, 1], [0, 2], [1, 2]]) {
      await env.DB.prepare("UPDATE cells SET state = 'vantar', owner_id = ?, owner_login = ?, uploaded_at = ? WHERE sheet = 'goteborg-norr' AND col = ? AND row = ?")
        .bind(other.github_id, other.login, NOW, col, row)
        .run();
    }
    expect((await claimCell(env.DB, other, 'goteborg-norr', 0, 0, NOW)).ok).toBe(true);
  });

  it('holds the lock limit under parallel claims on different cells', async () => {
    const results = await Promise.all([
      claimCell(env.DB, user, 'goteborg-norr', 0, 1, NOW),
      claimCell(env.DB, user, 'goteborg-norr', 1, 1, NOW),
      claimCell(env.DB, user, 'goteborg-norr', 2, 1, NOW),
    ]);
    expect(results.filter((r) => r.ok)).toHaveLength(1);
    const held = await env.DB.prepare("SELECT COUNT(*) AS n FROM cells WHERE owner_id = 4242 AND state = 'last'").first();
    expect(held.n).toBe(1);
  });

  it('clears an old rejection when the cell is claimed again', async () => {
    await setCell('goteborg-norr', 0, 1, { rejected_at: '2026-11-01T00:00:00Z', comment: 'gammalt' });
    await claimCell(env.DB, user, 'goteborg-norr', 0, 1, NOW);
    expect(await getCell('goteborg-norr', 0, 1)).toMatchObject({ state: 'last', rejected_at: null, comment: null });
  });

  it('lets exactly one of two simultaneous claims win', async () => {
    const [a, b] = await Promise.all([
      claimCell(env.DB, user, 'goteborg-norr', 2, 1, NOW),
      claimCell(env.DB, other, 'goteborg-norr', 2, 1, NOW),
    ]);
    expect([a.ok, b.ok].filter(Boolean)).toHaveLength(1);
    const cell = await getCell('goteborg-norr', 2, 1);
    expect(cell.state).toBe('last');
    expect([4242, 7]).toContain(cell.owner_id);
  });

  it('logs an event', async () => {
    await claimCell(env.DB, user, 'goteborg-norr', 0, 1, NOW);
    const row = await env.DB.prepare("SELECT * FROM events WHERE action = 'claim'").first();
    expect(row).toMatchObject({ at: NOW, github_id: 4242, login: 'klang', sheet: 'goteborg-norr', col: 0, row: 1 });
  });
});

describe('releaseCell', () => {
  it('frees your own lock', async () => {
    await claimCell(env.DB, user, 'goteborg-norr', 0, 0, NOW);
    expect(await releaseCell(env.DB, user, 'goteborg-norr', 0, 0, '2026-11-17T13:00:00Z')).toEqual({ ok: true });
    const cell = await getCell('goteborg-norr', 0, 0);
    expect(cell).toMatchObject({ state: 'fri', owner_id: null, owner_login: null, locked_at: null, expires_at: null, updated_at: '2026-11-17T13:00:00Z' });
  });

  it('refuses another user’s lock', async () => {
    await claimCell(env.DB, other, 'goteborg-norr', 0, 0, NOW);
    expect(await releaseCell(env.DB, user, 'goteborg-norr', 0, 0, NOW)).toMatchObject({ ok: false, status: 403, code: 'not_yours' });
  });

  it('refuses after upload', async () => {
    await claimCell(env.DB, user, 'goteborg-norr', 0, 0, NOW);
    await setCell('goteborg-norr', 0, 0, { state: 'vantar', uploaded_at: NOW });
    expect(await releaseCell(env.DB, user, 'goteborg-norr', 0, 0, NOW)).toMatchObject({ ok: false, status: 409, code: 'already_uploaded' });
  });
});

describe('routes', () => {
  function asUser(path, body) {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ id: 4242, login: 'klang' }), { status: 200, headers: { 'content-type': 'application/json' } }));
    return call(new Request(`https://x/v1/${path}`, { method: 'POST', headers: { Authorization: 'Bearer t', 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
  }

  it('POST /v1/claim answers 201 with expires_at and validates the body', async () => {
    const r = await asUser('claim', { sheet: 'goteborg-norr', col: 1, row: 0 });
    expect(r.status).toBe(201);
    expect(await r.json()).toMatchObject({ sheet: 'goteborg-norr', col: 1, row: 0, expires_at: expect.stringMatching(/Z$/) });
    const bad = await asUser('claim', { sheet: 'goteborg-norr', col: -1, row: 'x' });
    expect(bad.status).toBe(400);
    expect((await bad.json()).error).toBe('bad_body');
    vi.unstubAllGlobals();
  });

  it('POST /v1/release answers ok', async () => {
    await claimCell(env.DB, user, 'goteborg-norr', 1, 0, NOW);
    const r = await asUser('release', { sheet: 'goteborg-norr', col: 1, row: 0 });
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ ok: true });
    vi.unstubAllGlobals();
  });
});
