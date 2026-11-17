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
