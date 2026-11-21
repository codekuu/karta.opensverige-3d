import { env } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';

describe('schema', () => {
  it('has the five tables', async () => {
    const { results } = await env.DB.prepare(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'd1_%' ORDER BY name",
    ).all();
    expect(results.map((r) => r.name)).toEqual(['cells', 'events', 'purge', 'sheets', 'users']);
  });

  it('refuses a cell state outside the spec', async () => {
    await env.DB.prepare(
      "INSERT INTO sheets (id, city, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, pipeline, state, queue_order) VALUES ('t', 't', 'T', 1, 1, 0, 0, 0, 0, 0, 1, 'p', 'open', 1)",
    ).run();
    await expect(
      env.DB.prepare("INSERT INTO cells (sheet, col, row, state, updated_at) VALUES ('t', 0, 0, 'bogus', 'x')").run(),
    ).rejects.toThrow(/CHECK/);
  });
});
