import { createExecutionContext, env, waitOnExecutionContext } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import worker from '../src/index.js';
import { resolveUser } from '../src/auth.js';

const NOW = '2026-11-17T12:00:00Z';
const GH = { id: 4242, login: 'klang' };

function withToken(token, extra = {}) {
  return new Request('https://x/v1/me', { headers: { Authorization: `Bearer ${token}`, ...extra } });
}

/** Stands in for GitHub: records every call and answers with the given status and body. */
const calls = [];
function githubAnswers(status, body) {
  vi.stubGlobal('fetch', async (url, init) => {
    calls.push({ url: String(url), auth: init?.headers?.Authorization });
    if (String(url) !== 'https://api.github.com/user') throw new Error('unexpected fetch ' + url);
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json' },
    });
  });
}

async function clearDb() {
  await env.DB.batch([
    env.DB.prepare('DELETE FROM events'),
    env.DB.prepare('DELETE FROM cells'),
    env.DB.prepare('DELETE FROM users'),
    env.DB.prepare('DELETE FROM sheets'),
  ]);
}

beforeEach(async () => {
  calls.length = 0;
  await clearDb();
  const keys = await env.GH.list();
  await Promise.all(keys.keys.map((k) => env.GH.delete(k.name)));
});

afterEach(() => vi.unstubAllGlobals());

describe('resolveUser', () => {
  it('caches the github user for 600 s and does not ask twice', async () => {
    githubAnswers(200, GH);
    const first = await resolveUser(withToken('tok-a'), env, NOW);
    expect(first.user.login).toBe('klang');
    expect(first.user.github_id).toBe(4242);
    const second = await resolveUser(withToken('tok-a'), env, NOW);
    expect(second.user.login).toBe('klang');
    expect(calls).toHaveLength(1);
    const stored = await env.GH.list();
    expect(stored.keys).toHaveLength(1);
    expect(stored.keys[0].name.startsWith('gh:')).toBe(true);
    expect(stored.keys[0].name).not.toContain('tok-a');
    expect(stored.keys[0].expiration).toBeGreaterThan(Date.now() / 1000 + 500);
  });

  it('does not cache a token github rejects', async () => {
    githubAnswers(401, { message: 'Bad credentials' });
    const r = await resolveUser(withToken('tok-bad'), env, NOW);
    expect(r instanceof Response).toBe(true);
    expect(r.status).toBe(401);
    expect((await r.json()).error).toBe('bad_token');
    expect((await env.GH.list()).keys).toHaveLength(0);
  });

  it('reads a github outage as 503, not as not logged in', async () => {
    githubAnswers(502, 'bad gateway');
    const r = await resolveUser(withToken('tok-c'), env, NOW);
    expect(r.status).toBe(503);
    expect((await r.json()).error).toBe('github_down');
  });

  it('reads a github rate limit (403, remaining 0) as 503, not as a bad token', async () => {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ message: 'API rate limit exceeded' }), { status: 403, headers: { 'content-type': 'application/json', 'x-ratelimit-remaining': '0' } }));
    const r = await resolveUser(withToken('tok-rl'), env, NOW);
    expect(r.status).toBe(503);
    expect((await r.json()).error).toBe('github_down');
  });

  it('reads a 403 from github as an outage: a login without scopes is never short of rights', async () => {
    githubAnswers(403, { message: 'Forbidden' });
    const r = await resolveUser(withToken('tok-403'), env, NOW);
    expect(r.status).toBe(503);
    expect((await r.json()).error).toBe('github_down');
  });

  it('writes the user row once per lookup, not on every request', async () => {
    githubAnswers(200, GH);
    await resolveUser(withToken('tok-w'), env, NOW);
    await env.DB.prepare("UPDATE users SET login = 'changed-by-hand' WHERE github_id = 4242").run();
    const again = await resolveUser(withToken('tok-w'), env, NOW);
    // The cached identity did not rewrite the row.
    expect(again.user.login).toBe('changed-by-hand');
  });

  it('makes the row for an identity that is cached but not yet in the database', async () => {
    githubAnswers(200, GH);
    await resolveUser(withToken('tok-x'), env, NOW);
    await env.DB.prepare('DELETE FROM users').run();
    const again = await resolveUser(withToken('tok-x'), env, NOW);
    expect(again.user.github_id).toBe(4242);
  });

  describe('with the OAuth app secret set', () => {
    const bound = { ...env, GITHUB_CLIENT_ID: 'Ov23test', GITHUB_CLIENT_SECRET: 'shh' };
    function appAnswers(status, body) {
      vi.stubGlobal('fetch', async (url, init) => {
        calls.push({ url: String(url), auth: init?.headers?.Authorization, method: init?.method, body: init?.body });
        if (String(url) !== 'https://api.github.com/applications/Ov23test/token') throw new Error('unexpected fetch ' + url);
        return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
      });
    }

    it('accepts a token that our own app issued', async () => {
      appAnswers(200, { user: GH, app: { client_id: 'Ov23test' } });
      const r = await resolveUser(withToken('tok-ours'), bound, NOW);
      expect(r.user.login).toBe('klang');
      expect(calls).toHaveLength(1);
      expect(calls[0].method).toBe('POST');
      expect(calls[0].auth).toBe('Basic ' + btoa('Ov23test:shh'));
      expect(JSON.parse(calls[0].body)).toEqual({ access_token: 'tok-ours' });
    });

    it('refuses a token that another app or a person made', async () => {
      // Any site where someone signed in with GitHub holds a token GitHub vouches for. It is not ours.
      appAnswers(404, { message: 'Not Found' });
      const r = await resolveUser(withToken('tok-theirs'), bound, NOW);
      expect(r.status).toBe(401);
      expect((await r.json()).error).toBe('bad_token');
      expect((await env.GH.list()).keys).toHaveLength(0);
    });

    it('reads an outage of that check as 503', async () => {
      appAnswers(500, { message: 'oops' });
      const r = await resolveUser(withToken('tok-out'), bound, NOW);
      expect(r.status).toBe(503);
    });
  });

  it('is 401 no_token without a bearer header', async () => {
    const r = await resolveUser(new Request('https://x/v1/me'), env, NOW);
    expect(r.status).toBe(401);
    expect((await r.json()).error).toBe('no_token');
  });

  it('upserts the user row on first sight', async () => {
    githubAnswers(200, GH);
    await resolveUser(withToken('tok-d'), env, NOW);
    const row = await env.DB.prepare('SELECT * FROM users WHERE github_id = ?').bind(4242).first();
    expect(row).toMatchObject({ login: 'klang', first_seen: NOW, approved_count: 0, trusted: 0, blocked: 0 });
  });

  it('refuses a blocked user with 403', async () => {
    await env.DB.prepare("INSERT INTO users (github_id, login, first_seen, blocked) VALUES (4242, 'klang', ?, 1)").bind(NOW).run();
    githubAnswers(200, GH);
    const r = await resolveUser(withToken('tok-e'), env, NOW);
    expect(r.status).toBe(403);
    expect((await r.json()).error).toBe('blocked');
  });

  it('recognises the admin by github id', async () => {
    githubAnswers(200, GH);
    const r = await resolveUser(withToken('tok-f'), { ...env, ADMIN_GITHUB_ID: '4242' }, NOW);
    expect(r.isAdmin).toBe(true);
    githubAnswers(200, { id: 7, login: 'other' });
    const other = await resolveUser(withToken('tok-g'), { ...env, ADMIN_GITHUB_ID: '4242' }, NOW);
    expect(other.isAdmin).toBe(false);
  });
});

describe('GET /v1/me', () => {
  it('returns the user and their locks', async () => {
    githubAnswers(200, GH);
    await env.DB.prepare(
      "INSERT INTO sheets (id, city, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, pipeline, state, queue_order) VALUES ('goteborg-norr', 'goteborg', 'Göteborg', 2, 1, 57.707, 11.967, -16384, 13312, 40, 18, 'p', 'open', 1)",
    ).run();
    await env.DB.batch([
      env.DB.prepare("INSERT INTO cells (sheet, col, row, state, owner_id, owner_login, updated_at) VALUES ('goteborg-norr', 0, 0, 'last', 4242, 'klang', ?)").bind(NOW),
      env.DB.prepare("INSERT INTO cells (sheet, col, row, state, updated_at) VALUES ('goteborg-norr', 1, 0, 'fri', ?)").bind(NOW),
    ]);
    const ctx = createExecutionContext();
    const r = await worker.fetch(withToken('tok-h'), { ...env, TAKT: { limit: async () => ({ success: true }) } }, ctx);
    await waitOnExecutionContext(ctx);
    expect(r.status).toBe(200);
    const body = await r.json();
    expect(body).toMatchObject({ login: 'klang', github_id: 4242, trusted: 0, approved_count: 0 });
    expect(body.locks).toHaveLength(1);
    expect(body.locks[0]).toMatchObject({ sheet: 'goteborg-norr', col: 0, row: 0, state: 'last' });
  });
});
