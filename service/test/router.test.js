import { SELF } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';

describe('router', () => {
  it('health answers json', async () => {
    const r = await SELF.fetch('https://x/v1/health');
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ ok: true, service: 'pixelbygget' });
  });

  it('unknown path is 404 json', async () => {
    const r = await SELF.fetch('https://x/v1/nope');
    expect(r.status).toBe(404);
    expect((await r.json()).error).toBe('not_found');
  });
});
