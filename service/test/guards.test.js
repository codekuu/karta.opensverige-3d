import { createExecutionContext, env, waitOnExecutionContext } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import worker from '../src/index.js';

/** Calls the worker with an env override, the way SELF.fetch cannot. */
async function call(request, overrides = {}) {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, { ...env, ...overrides }, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}

const allow = { limit: async () => ({ success: true }) };
const deny = { limit: async () => ({ success: false }) };

describe('kill switch', () => {
  it('gives 503 on POST and lets GET pass', async () => {
    const post = await call(new Request('https://x/v1/claim', { method: 'POST' }), { PIXELBYGGET_STANGD: '1', TAKT: allow });
    expect(post.status).toBe(503);
    expect(post.headers.get('Retry-After')).toBe('300');
    expect((await post.json()).error).toBe('closed');
    const get = await call(new Request('https://x/v1/health'), { PIXELBYGGET_STANGD: '1', TAKT: allow });
    expect(get.status).toBe(200);
  });
});

describe('rate limit', () => {
  it('is 429 with Retry-After 30 when the bucket says no', async () => {
    const r = await call(new Request('https://x/v1/health', { headers: { 'CF-Connecting-IP': '203.0.113.9' } }), { TAKT: deny });
    expect(r.status).toBe(429);
    expect(r.headers.get('Retry-After')).toBe('30');
    expect((await r.json()).error).toBe('rate_limited');
  });

  it('keys ipv6 on the /64', async () => {
    const seen = [];
    const spy = { limit: async ({ key }) => { seen.push(key); return { success: true }; } };
    await call(new Request('https://x/v1/health', { headers: { 'CF-Connecting-IP': '2001:db8:1:2:3:4:5:6' } }), { TAKT: spy });
    await call(new Request('https://x/v1/health', { headers: { 'CF-Connecting-IP': '2001:db8:1:2:ffff:ffff:ffff:ffff' } }), { TAKT: spy });
    expect(seen[0]).toBe(seen[1]);
    expect(seen[0]).toBe('2001:0db8:0001:0002::/64');
  });
});

describe('failures', () => {
  it('turns a thrown handler error into json 500 with cors', async () => {
    const broken = { prepare: () => { throw new Error('boom'); } };
    const r = await call(new Request('https://x/v1/sheets', { headers: { Origin: 'https://karta.opensverige.se' } }), { TAKT: allow, DB: broken });
    expect(r.status).toBe(500);
    expect((await r.json()).error).toBe('internal');
    expect(r.headers.get('Access-Control-Allow-Origin')).toBe('https://karta.opensverige.se');
  });

  it('carries cors on a rate-limited GET', async () => {
    const r = await call(new Request('https://x/v1/health', { headers: { Origin: 'https://karta.opensverige.se', 'CF-Connecting-IP': '203.0.113.9' } }), { TAKT: deny });
    expect(r.status).toBe(429);
    expect(r.headers.get('Access-Control-Allow-Origin')).toBe('https://karta.opensverige.se');
  });
});

describe('cors', () => {
  it('echoes an allowed origin on GET and nothing for others', async () => {
    const ok = await call(new Request('https://x/v1/health', { headers: { Origin: 'https://karta.opensverige.se' } }), { TAKT: allow });
    expect(ok.headers.get('Access-Control-Allow-Origin')).toBe('https://karta.opensverige.se');
    expect(ok.headers.get('Vary')).toContain('Origin');
    const bad = await call(new Request('https://x/v1/health', { headers: { Origin: 'https://evil.example' } }), { TAKT: allow });
    expect(bad.headers.get('Access-Control-Allow-Origin')).toBeNull();
  });

  it('answers preflight with 204 and GET only', async () => {
    const r = await call(new Request('https://x/v1/grid/goteborg-norr', { method: 'OPTIONS', headers: { Origin: 'https://karta.opensverige.se', 'Access-Control-Request-Method': 'GET' } }), { TAKT: allow });
    expect(r.status).toBe(204);
    expect(r.headers.get('Access-Control-Allow-Methods')).toBe('GET');
    expect(r.headers.get('Access-Control-Allow-Origin')).toBe('https://karta.opensverige.se');
  });

  it('adds no cors header to a POST', async () => {
    const r = await call(new Request('https://x/v1/nope', { method: 'POST', headers: { Origin: 'https://karta.opensverige.se' } }), { TAKT: allow });
    expect(r.headers.get('Access-Control-Allow-Origin')).toBeNull();
  });
});
