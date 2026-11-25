/* One owner per piece of ground: where two sheets of the tiling meet, a cell is offered by the
   sheet whose box holds its middle, and by no other. */

import { env } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { claimCell } from '../src/cells.js';
import { placeAt } from '../src/sheets.js';
import { LAT_STEP, LON_STEP, cellAt, cellCentre, ownedCellAt, owns, sheetAt } from '../src/tiling.js';
import { NOW, clearDb, insertUser } from './fixtures.js';

const user = { github_id: 4242, login: 'klang', first_seen: NOW, approved_count: 0, trusted: 0, blocked: 0 };

/** Points on and around the line between two boxes, along the whole line. */
function nearLines(tile) {
  const south = tile.origin_lat - LAT_STEP / 2;
  const west = tile.origin_lon - LON_STEP / 2;
  const pts = [];
  for (let i = 0; i <= 40; i += 1) {
    for (const d of [-0.0004, -0.00005, 0, 0.00005, 0.0004]) {
      pts.push([south + d, west + (LON_STEP * i) / 40]);
      pts.push([south + (LAT_STEP * i) / 40, west + d]);
    }
  }
  return pts;
}

describe('owns', () => {
  it('keeps the published Helsingborg cell on its own sheet', () => {
    const tile = sheetAt(56.0465, 12.6945);
    expect(tile.id).toBe('se-n5602-e1262');
    expect(owns(tile, 47, 16)).toBe(true);
  });

  it('lets no more than the sheets meeting at a corner show the same ground', () => {
    // Pictures of neighbouring boxes overlap by up to half a cell at the line, and leave a thin
    // strip uncovered at the south corners of a box: a seam, which is accepted. ownedCellAt
    // gives such a point the nearest owned cell.
    const tile = sheetAt(56.0465, 12.6945);
    for (const [lat, lon] of nearLines(tile)) {
      let owners = 0;
      for (let a = -1; a <= 1; a += 1) {
        for (let b = -1; b <= 1; b += 1) {
          const s = sheetAt(lat + a * LAT_STEP, lon + b * LON_STEP);
          const c = s && cellAt(s, lat, lon);
          if (c && owns(s, c.col, c.row)) owners += 1;
        }
      }
      expect(owners, `${lat},${lon}`).toBeLessThanOrEqual(4);
    }
  });

  it('is false for a cell outside the sheet', () => {
    const tile = sheetAt(56.0465, 12.6945);
    expect(owns(tile, -1, 0)).toBe(false);
    expect(owns(tile, tile.cols, 0)).toBe(false);
    expect(owns(tile, 0, tile.rows)).toBe(false);
  });

  it('gives each cell middle to the sheet whose box holds it', () => {
    const tile = sheetAt(56.0465, 12.6945);
    for (const [col, row] of [[0, 0], [60, 46], [0, 46], [60, 0], [30, 23]]) {
      const m = cellCentre(tile, col, row);
      expect(owns(tile, col, row)).toBe(sheetAt(m.lat, m.lon).id === tile.id);
    }
  });
});

describe('ownedCellAt', () => {
  it('answers a cell its sheet owns, for every point near a line between boxes', () => {
    const tile = sheetAt(56.0465, 12.6945);
    for (const [lat, lon] of nearLines(tile)) {
      const got = ownedCellAt(lat, lon);
      expect(got, `${lat},${lon}`).not.toBeNull();
      expect(owns(got.tile, got.col, got.row), `${lat},${lon}`).toBe(true);
    }
  });

  it('answers the cell that holds the point wherever one is owned', () => {
    const got = ownedCellAt(56.0465, 12.6945);
    expect([got.tile.id, got.col, got.row]).toEqual(['se-n5602-e1262', 47, 16]);
  });

  it('is null outside Sweden', () => {
    expect(ownedCellAt(59.9139, 10.7522)).toBeNull();
  });
});

describe('a claim on ground another sheet owns', () => {
  beforeEach(async () => {
    await clearDb();
    await insertUser(user);
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ elevation: [10] }), { status: 200 }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('place never answers a cell a claim would refuse', async () => {
    const tile = sheetAt(56.0465, 12.6945);
    for (const [lat, lon] of nearLines(tile).filter((_, i) => i % 7 === 0)) {
      const p = await placeAt(env.DB, '2026.11-a', lat, lon);
      if (!p || p.sheet.made !== 'grid') continue;
      const t = sheetAt(p.sheet.origin_lat, p.sheet.origin_lon);
      expect(owns(t, p.col, p.row), `${lat},${lon} -> ${p.sheet.id} ${p.col}_${p.row}`).toBe(true);
    }
  });
});
