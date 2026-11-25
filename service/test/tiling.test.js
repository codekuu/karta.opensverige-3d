import { describe, expect, it } from 'vitest';
import { CELL_GROUND_NORTH, CELL_METERS, LAT_STEP, LON_STEP, SWEDEN, cellAt, sheetAt, sheetById } from '../src/tiling.js';

const HELSINGBORG = [56.0465, 12.6945];
const OSLO = [59.9139, 10.7522];

describe('sheetAt', () => {
  it('gives Helsingborg one sheet, and a point 100 m away the same one', () => {
    const here = sheetAt(...HELSINGBORG);
    expect(here.id).toBe('se-n5602-e1262');
    expect(sheetAt(HELSINGBORG[0] + 0.0009, HELSINGBORG[1]).id).toBe(here.id);
    expect(sheetAt(HELSINGBORG[0], HELSINGBORG[1] - 0.0016).id).toBe(here.id);
  });

  it('puts the origin in the middle of the box, and the picture around it', () => {
    expect(sheetAt(...HELSINGBORG)).toEqual({
      id: 'se-n5602-e1262',
      origin_lat: 56.025,
      origin_lon: 12.625,
      cols: 61,
      rows: 47,
      x_meters: -7808,
      y_meters: 6016,
    });
  });

  it('is null outside Sweden and for what is not a point', () => {
    expect(sheetAt(...OSLO)).toBeNull();
    expect(sheetAt(48.85, 2.35)).toBeNull();
    expect(sheetAt(NaN, 12)).toBeNull();
    expect(sheetAt('56', 12)).toBeNull();
    expect(sheetAt(56, Infinity)).toBeNull();
  });

  it('is 61 cells wide at 56 degrees and 41 at 68, and 47 high everywhere', () => {
    expect(sheetAt(56.03, 14.1).cols).toBe(61);
    expect(sheetAt(68.03, 20.1).cols).toBe(41);
    for (let lat = SWEDEN.latMin + 0.2; lat < SWEDEN.latMax - 0.2; lat += 0.4) {
      expect(sheetAt(lat, 15.1).rows).toBe(47);
    }
  });

  it('keeps to the steps and the cell size the plan names', () => {
    expect([LAT_STEP, LON_STEP, CELL_METERS]).toEqual([0.15, 0.25, 256]);
    expect(CELL_GROUND_NORTH).toBeCloseTo(362.04, 2);
  });

  it('gives a point on a box edge to the box north and east of it', () => {
    expect(sheetAt(56.1, 12.75).id).toBe('se-n5617-e1287');
    expect(sheetAt(56.099999, 12.749999).id).toBe('se-n5602-e1262');
  });
});

describe('sheetById', () => {
  it('answers what sheetAt answers, for every box in Sweden', () => {
    let boxes = 0;
    for (let lat = SWEDEN.latMin - 0.3; lat < SWEDEN.latMax + 0.3; lat += 0.05) {
      for (let lon = SWEDEN.lonMin - 0.3; lon < SWEDEN.lonMax + 0.3; lon += 0.09) {
        const at = sheetAt(lat, lon);
        if (!at) continue;
        boxes += 1;
        expect(sheetById(at.id)).toEqual(at);
      }
    }
    expect(boxes).toBeGreaterThan(10000);
  });

  it('is null for an id whose numbers are not on the steps', () => {
    expect(sheetById('se-n5602-e1262')).not.toBeNull();
    expect(sheetById('se-n5601-e1263')).toBeNull();
    expect(sheetById('se-n5602-e1263')).toBeNull();
    expect(sheetById('se-n5603-e1262')).toBeNull();
  });

  it('is null for a sheet on the steps that lies outside Sweden', () => {
    expect(sheetById('se-n5002-e1262')).toBeNull();
    expect(sheetById('se-n5602-e0512')).toBeNull();
  });

  it('is null for an id of another shape', () => {
    for (const id of ['goteborg-norr', 'stockholm-norr', 'se-n56020-e1262', 'se-n5602-e1262 ', 'SE-N5602-E1262', 'se-n5602-e1262-x', '', null, 5602]) {
      expect(sheetById(id)).toBeNull();
    }
  });
});

describe('cellAt', () => {
  it('puts the centre of a grid sheet in its middle cell', () => {
    const sheet = sheetAt(...HELSINGBORG);
    expect(cellAt(sheet, sheet.origin_lat, sheet.origin_lon)).toEqual({ col: 30, row: 23 });
  });

  it('gives row 0 to the north and col 0 to the west', () => {
    const sheet = sheetAt(...HELSINGBORG);
    expect(cellAt(sheet, 56.0995, 12.501)).toEqual({ col: 0, row: 0 });
    expect(cellAt(sheet, 55.9505, 12.749)).toEqual({ col: 60, row: 46 });
  });

  // The numbers are what cli/rutor/frame.py answers for the same sheet and points
  // (SheetFrame.map_pixel at the sheet's ground height, divided by 1024): the renderer's frame.
  it('agrees with the frame the renderer draws in, on a hand-made sheet', () => {
    const sheet = { origin_lat: 57.707, origin_lon: 11.967, x_meters: -16384, y_meters: 13312, ground_height: 40, cols: 128, rows: 104 };
    expect(cellAt(sheet, 57.7089, 11.9746)).toEqual({ col: 65, row: 51 });
    expect(cellAt(sheet, 57.8, 11.75)).toEqual({ col: 13, row: 23 });
    expect(cellAt(sheet, 57.62, 12.2)).toEqual({ col: 118, row: 78 });
    expect(cellAt(sheet, 57.8725, 11.7)).toEqual({ col: 2, row: 1 });
  });

  it('is null for a point outside the picture', () => {
    const sheet = { origin_lat: 57.707, origin_lon: 11.967, x_meters: -16384, y_meters: 13312, ground_height: 40, cols: 128, rows: 104 };
    expect(cellAt(sheet, 57.9, 11.967)).toBeNull();
    expect(cellAt(sheet, 57.707, 13.5)).toBeNull();
    expect(cellAt(sheet, 59.33, 18.06)).toBeNull();
  });

  it('counts the ground height: the same point lands further up the picture on high ground', () => {
    const low = { origin_lat: 63.175, origin_lon: 14.625, x_meters: -6528, y_meters: 6016, ground_height: 0, cols: 51, rows: 47 };
    const high = { ...low, ground_height: 362 };
    expect(cellAt(low, 63.175, 14.625)).toEqual({ col: 25, row: 23 });
    expect(cellAt(high, 63.175, 14.625)).toEqual({ col: 25, row: 22 });
  });
});
