/* What a review of the service found, each as the request that went wrong. */

import { env } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setBlock } from '../src/admin.js';
import { resolveUser } from '../src/auth.js';
import { expireLocks } from '../src/cron.js';
import { getGrid } from '../src/sheets.js';
import { NOW, call, clearDb, getCell, insertCells, insertSheet, insertUser, setCell } from './fixtures.js';
import { manifestHeader, minimalPng } from './png.js';

const LATER = '2026-11-17T13:00:00Z';

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function withToken(token) {
  return new Request('https://x/v1/me', { headers: { Authorization: `Bearer ${token}` } });
}

async function emptyInbox() {
  const list = await env.INKORG.list();
  await Promise.all(list.objects.map((o) => env.INKORG.delete(o.key)));
}

beforeEach(async () => {
  await clearDb();
  await emptyInbox();
});

afterEach(() => vi.unstubAllGlobals());

describe('a live cell the admin took down', () => {
  it('is not shown as free while nobody may claim it', async () => {
    await insertSheet({ cols: 2, rows: 1 });
    await insertCells('goteborg-norr', 2, 1);
    await setCell('goteborg-norr', 1, 0, { state: 'fri', removed_at: NOW, inbox_key: 'goteborg-norr/1_0.abc' });
    const grid = await getGrid(env.DB, 'goteborg-norr');
    expect(grid.cells).toEqual([
      [0, 0, 'fri', null, NOW],
      [1, 0, 'last', null, NOW],
    ]);
  });
});

describe('a token GitHub refused', () => {
  it('is not asked about again within the minute', async () => {
    let asked = 0;
    vi.stubGlobal('fetch', async () => {
      asked += 1;
      return new Response('{}', { status: 401 });
    });
    const first = await resolveUser(withToken('tok-bad-once'), env, NOW);
    const second = await resolveUser(withToken('tok-bad-once'), env, NOW);
    expect(first.status).toBe(401);
    expect(second.status).toBe(401);
    expect(asked).toBe(1);
  });

  it('is asked about again when the minute has passed', async () => {
    let asked = 0;
    vi.stubGlobal('fetch', async () => {
      asked += 1;
      return new Response('{}', { status: 401 });
    });
    await resolveUser(withToken('tok-bad-later'), env, NOW);
    await resolveUser(withToken('tok-bad-later'), env, '2026-11-17T12:02:00Z');
    expect(asked).toBe(2);
  });
});

describe('the login cache', () => {
  it('failing to answer sends the question to GitHub, not a 500', async () => {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ id: 4242, login: 'klang' }), { status: 200 }));
    const broken = {
      get: async () => {
        throw new Error('kv down');
      },
      put: async () => {
        throw new Error('kv down');
      },
    };
    const resolved = await resolveUser(withToken('tok-kv'), { ...env, GH: broken }, NOW);
    expect(resolved.user.login).toBe('klang');
  });
});

describe('own logins only', () => {
  it('with the secret set and no client id, nobody is let in and GitHub is not asked', async () => {
    let asked = 0;
    vi.stubGlobal('fetch', async () => {
      asked += 1;
      return new Response(JSON.stringify({ id: 4242, login: 'klang' }), { status: 200 });
    });
    const resolved = await resolveUser(withToken('tok-any'), { ...env, GITHUB_CLIENT_SECRET: 's', GITHUB_CLIENT_ID: '' }, NOW);
    expect(resolved.status).toBe(503);
    expect((await resolved.json()).error).toBe('misconfigured');
    expect(asked).toBe(0);
  });
});

describe('blocking a builder', () => {
  it('frees the cell they hold', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3);
    await insertUser();
    await setCell('goteborg-norr', 1, 1, { state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: NOW, expires_at: '2026-11-19T12:00:00Z' });
    expect(await setBlock(env.DB, { login: 'klang' }, 1, LATER)).toEqual({ ok: true });
    expect(await getCell('goteborg-norr', 1, 1)).toMatchObject({ state: 'fri', owner_id: null, owner_login: null, expires_at: null });
  });

  it('leaves their waiting upload for the reviewer', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3);
    await insertUser();
    await setCell('goteborg-norr', 2, 1, { state: 'vantar', owner_id: 4242, owner_login: 'klang', uploaded_at: NOW });
    await setBlock(env.DB, { login: 'klang' }, 1, LATER);
    expect(await getCell('goteborg-norr', 2, 1)).toMatchObject({ state: 'vantar', owner_id: 4242 });
  });
});

describe('POST /live', () => {
  async function asAdmin(body) {
    await insertUser({ github_id: 1, login: 'admin' });
    await env.GH.put('gh:' + (await sha256Hex(new TextEncoder().encode('tok-admin'))), JSON.stringify({ id: 1, login: 'admin' }));
    return call(
      new Request('https://x/v1/live', { method: 'POST', headers: { Authorization: 'Bearer tok-admin', 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      { ADMIN_GITHUB_ID: '1' },
    );
  }

  it('refuses an empty list, so a revision cannot move with no cell', async () => {
    await insertSheet();
    const response = await asAdmin({ sheet: 'goteborg-norr', cells: [], rev: 5 });
    expect(response.status).toBe(400);
    expect((await env.DB.prepare('SELECT rev FROM sheets WHERE id = ?').bind('goteborg-norr').first()).rev).toBe(0);
  });

  it('refuses a revision no later one could follow', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3);
    await setCell('goteborg-norr', 0, 0, { state: 'godkand' });
    const response = await asAdmin({ sheet: 'goteborg-norr', cells: [[0, 0]], rev: 1e300 });
    expect(response.status).toBe(400);
    expect((await getCell('goteborg-norr', 0, 0)).state).toBe('godkand');
  });
});

describe('an upload that fails after the picture is stored', () => {
  it('takes its own picture away again', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3);
    await insertUser();
    await env.GH.put('gh:' + (await sha256Hex(new TextEncoder().encode('tok-up'))), JSON.stringify({ id: 4242, login: 'klang' }));
    await setCell('goteborg-norr', 1, 1, { state: 'last', owner_id: 4242, owner_login: 'klang', locked_at: NOW, expires_at: '2099-01-01T00:00:00Z' });
    const png = minimalPng(1024, 1024);
    const real = env.INKORG;
    const inbox = {
      put: async (key, value, options) => {
        if (key.endsWith('.json')) throw new Error('r2 down');
        return real.put(key, value, options);
      },
      delete: (keys) => real.delete(keys),
      get: (key) => real.get(key),
      list: () => real.list(),
    };
    const request = new Request('https://x/v1/upload/goteborg-norr/1_1', {
      method: 'PUT',
      headers: { Authorization: 'Bearer tok-up', 'Content-Length': String(png.length), 'X-Manifest': manifestHeader({ sha256: await sha256Hex(png) }) },
      body: png,
    });
    const response = await call(request, { INKORG: inbox });
    expect(response.status).toBe(500);
    expect((await real.list()).objects.map((o) => o.key)).toEqual([]);
    expect((await getCell('goteborg-norr', 1, 1)).state).toBe('last');
  });
});

describe('expireLocks', () => {
  it('counts the locks it freed, not the ones it read', async () => {
    await insertSheet();
    await insertCells('goteborg-norr', 4, 3);
    await setCell('goteborg-norr', 0, 0, { state: 'last', owner_id: 4242, owner_login: 'klang', expires_at: '2026-11-17T11:00:00Z' });
    expect(await expireLocks(env.DB, NOW)).toBe(1);
    expect(await expireLocks(env.DB, NOW)).toBe(0);
  });
});
