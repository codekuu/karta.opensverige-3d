/* The tiling: the country cut into fixed sheets by a rule, so nobody opens a sheet by hand.

   A sheet covers LAT_STEP degrees of latitude and LON_STEP of longitude, on whole steps from
   0, and its origin is the middle of that box. Two people who click in the same town land on
   the same sheet. Neighbouring sheets meet with a seam of a few metres: each has its own
   camera, and that is accepted.

   The id is se-n<lat>-e<lon>: the origin's hundredths of a degree, cut, not rounded
   (56.025, 12.625 is se-n5602-e1262). The numbers of a real id are 7 more than a multiple of
   15, and 12 more than a multiple of 25; any other id is not a sheet.

   A cell is 1024 px at 0.25 m. The picture is seen 45 degrees down, so a cell covers 256 m
   east to west and 256 * sqrt(2) m north to south. cellAt is the frame the renderer draws in
   (cli/rutor/frame.py), heading 0. */

export const LAT_STEP = 0.15;
export const LON_STEP = 0.25;
export const CELL_METERS = 256;
export const CELL_GROUND_NORTH = 256 * Math.SQRT2;
const CELL_PIXELS = 1024;
/** A box around Sweden. It holds some of the neighbours too: every cell is reviewed anyway. */
export const SWEDEN = { latMin: 55.2, latMax: 69.1, lonMin: 10.9, lonMax: 24.2 };

const METRES_PER_DEGREE = 111320;
// Whole millionths of a degree, so a box edge is never decided by a rounding in the last bit.
const MICRO = 1e6;
const LAT_MICRO = 150000;
const LON_MICRO = 250000;
const GRID_ID = /^se-n(\d{4})-e(\d{4})$/;
const WGS84_A = 6378137;
const WGS84_F = 1 / 298.257223563;
const WGS84_E2 = WGS84_F * (2 - WGS84_F);

/**
 * @typedef {{ id: string, origin_lat: number, origin_lon: number, cols: number, rows: number,
 *   x_meters: number, y_meters: number }} Tile
 */

/**
 * The sheet whose box has its middle at these millionths of a degree, or null outside Sweden.
 * @param {number} latMicro
 * @param {number} lonMicro
 * @returns {Tile | null}
 */
function tile(latMicro, lonMicro) {
  const origin_lat = latMicro / MICRO;
  const origin_lon = lonMicro / MICRO;
  if (origin_lat < SWEDEN.latMin || origin_lat > SWEDEN.latMax || origin_lon < SWEDEN.lonMin || origin_lon > SWEDEN.lonMax) return null;
  const cols = Math.ceil((LON_STEP * METRES_PER_DEGREE * Math.cos((origin_lat * Math.PI) / 180)) / CELL_METERS);
  const rows = Math.ceil((LAT_STEP * METRES_PER_DEGREE) / CELL_GROUND_NORTH);
  const n = String(Math.floor(latMicro / 10000)).padStart(4, '0');
  const e = String(Math.floor(lonMicro / 10000)).padStart(4, '0');
  return { id: `se-n${n}-e${e}`, origin_lat, origin_lon, cols, rows, x_meters: (-cols * CELL_METERS) / 2, y_meters: (rows * CELL_METERS) / 2 };
}

/**
 * The sheet a point lies on. A point is in Sweden when the middle of its box is: so a sheet
 * is either whole or not there, and sheetById agrees.
 * @param {number} lat
 * @param {number} lon
 * @returns {Tile | null}
 */
export function sheetAt(lat, lon) {
  if (typeof lat !== 'number' || typeof lon !== 'number' || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const south = Math.floor(Math.round(lat * MICRO) / LAT_MICRO) * LAT_MICRO;
  const west = Math.floor(Math.round(lon * MICRO) / LON_MICRO) * LON_MICRO;
  return tile(south + LAT_MICRO / 2, west + LON_MICRO / 2);
}

/**
 * The sheet with this id, or null when the id is not one the tiling gives out.
 * @param {unknown} id
 * @returns {Tile | null}
 */
export function sheetById(id) {
  const found = typeof id === 'string' ? GRID_ID.exec(id) : null;
  if (!found) return null;
  // The numbers name a point; the id is real when that point's own sheet carries it.
  const sheet = sheetAt((Number(found[1]) * 10000 + 5000) / MICRO, (Number(found[2]) * 10000 + 5000) / MICRO);
  return sheet && sheet.id === id ? sheet : null;
}

/**
 * A point in the earth-centred frame.
 * @param {number} latDeg
 * @param {number} lonDeg
 * @param {number} height metres above the ellipsoid
 * @returns {[number, number, number]}
 */
function ecef(latDeg, lonDeg, height) {
  const lat = (latDeg * Math.PI) / 180;
  const lon = (lonDeg * Math.PI) / 180;
  const n = WGS84_A / Math.sqrt(1 - WGS84_E2 * Math.sin(lat) ** 2);
  return [(n + height) * Math.cos(lat) * Math.cos(lon), (n + height) * Math.cos(lat) * Math.sin(lon), (n * (1 - WGS84_E2) + height) * Math.sin(lat)];
}

/**
 * Where a point on the ground lands in a sheet's picture, counted in cells from the top left.
 * It may lie outside the picture. The ground is taken to lie at the sheet's ground height
 * (0 when it has none).
 * @param {{ origin_lat: number, origin_lon: number, x_meters: number, y_meters: number, ground_height?: number }} sheet
 * @param {number} lat
 * @param {number} lon
 * @returns {{ col: number, row: number }}
 */
function pictureCell(sheet, lat, lon) {
  const height = Number.isFinite(sheet.ground_height) ? sheet.ground_height : 0;
  const la = (sheet.origin_lat * Math.PI) / 180;
  const lo = (sheet.origin_lon * Math.PI) / 180;
  const east = [-Math.sin(lo), Math.cos(lo), 0];
  const north = [-Math.sin(la) * Math.cos(lo), -Math.sin(la) * Math.sin(lo), Math.cos(la)];
  const up = [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo), Math.sin(la)];
  const o = ecef(sheet.origin_lat, sheet.origin_lon, 0);
  const p = ecef(lat, lon, height);
  const d = [p[0] - o[0], p[1] - o[1], p[2] - o[2]];
  const dot = (v) => d[0] * v[0] + d[1] * v[1] + d[2] * v[2];
  // The camera looks north and 45 degrees down: its up is half north, half up.
  const x = dot(east);
  const y = (dot(north) + dot(up)) * Math.SQRT1_2;
  return { col: Math.floor((x - sheet.x_meters) / CELL_METERS), row: Math.floor((sheet.y_meters - y) / CELL_METERS) };
}

/**
 * The cell of a sheet that shows a point on the ground, or null when the point is outside the
 * picture. Row 0 is the top of the picture, the north.
 * @param {{ origin_lat: number, origin_lon: number, x_meters: number, y_meters: number, cols: number, rows: number, ground_height?: number }} sheet
 * @param {number} lat
 * @param {number} lon
 * @returns {{ col: number, row: number } | null}
 */
export function cellAt(sheet, lat, lon) {
  const { col, row } = pictureCell(sheet, lat, lon);
  if (!(col >= 0 && row >= 0 && col < sheet.cols && row < sheet.rows)) return null;
  return { col, row };
}

/**
 * The cell of a sheet nearest a point: cellAt, but a point just outside the picture gets the
 * cell at the edge. A box of the tiling is a hair wider at its south edge than its picture,
 * and every point of the box must still get a cell.
 * @param {{ origin_lat: number, origin_lon: number, x_meters: number, y_meters: number, cols: number, rows: number, ground_height?: number }} sheet
 * @param {number} lat
 * @param {number} lon
 * @returns {{ col: number, row: number }}
 */
export function nearestCell(sheet, lat, lon) {
  const { col, row } = pictureCell(sheet, lat, lon);
  return { col: Math.min(Math.max(col, 0), sheet.cols - 1), row: Math.min(Math.max(row, 0), sheet.rows - 1) };
}

/**
 * The whole row of a sheet of the tiling, as the table holds it. The origin is on the
 * ellipsoid and the camera looks 45 degrees down, so ground at a height lands height / sqrt(2)
 * further up the picture: the picture's top moves by as much, and the box stays in the middle
 * of the picture whatever the height. Before the row is made nobody has asked for the height;
 * 0 with the matching top puts the grid at the same place on the ground.
 * @param {Tile} tile
 * @param {string} pipeline
 * @param {number} [groundHeight] metres above the ellipsoid
 * @returns {import('./db.js').Sheet}
 */
export function gridRow(tile, pipeline, groundHeight = 0) {
  const [, n, e] = GRID_ID.exec(tile.id);
  return {
    id: tile.id,
    city: 'se',
    name: `N ${n.slice(0, 2)},${n.slice(2)} E ${e.slice(0, 2)},${e.slice(2)}`,
    cols: tile.cols,
    rows: tile.rows,
    origin_lat: tile.origin_lat,
    origin_lon: tile.origin_lon,
    x_meters: tile.x_meters,
    y_meters: tile.y_meters + groundHeight * Math.SQRT1_2,
    ground_height: groundHeight,
    dzi_levels: Math.ceil(Math.log2(Math.max(tile.cols, tile.rows) * CELL_PIXELS)) + 1,
    pipeline,
    state: 'open',
    queue_order: 100,
    rev: 0,
    kind: 'google',
    made: 'grid',
  };
}
