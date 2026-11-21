/* A minimal PNG for tests: signature, IHDR, one IDAT, IEND, all with real CRCs. The image
   data is not a decodable picture, but every chunk is well formed, as the service checks. */

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function u32(n) {
  return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
}

/**
 * One PNG chunk: length, type, data and CRC.
 * @param {string} type four ASCII letters
 * @param {number[]} data
 * @returns {number[]}
 */
export function pngChunk(type, data) {
  const typed = [...type].map((ch) => ch.charCodeAt(0));
  return [...u32(data.length), ...typed, ...data, ...u32(crc32([...typed, ...data]))];
}

/**
 * @param {number} width
 * @param {number} height
 * @param {number} [padding] bytes in an extra ancillary chunk, to reach a wanted size
 * @returns {Uint8Array}
 */
export function minimalPng(width, height, padding = 0) {
  const ihdr = pngChunk('IHDR', [...u32(width), ...u32(height), 8, 6, 0, 0, 0]);
  const pad = padding > 0 ? pngChunk('ruTa', new Array(padding).fill(0)) : [];
  const idat = pngChunk('IDAT', [0x78, 0x9c, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01]);
  const iend = pngChunk('IEND', []);
  return new Uint8Array([...SIGNATURE, ...ihdr, ...pad, ...idat, ...iend]);
}

/**
 * Hex sha256 of bytes.
 * @param {Uint8Array} bytes
 * @returns {Promise<string>}
 */
export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * A manifest for a cell upload, base64 encoded for the X-Manifest header. It has passed the
 * quality check unless an override says otherwise.
 * @param {object} overrides
 * @returns {string}
 */
export function manifestHeader(overrides = {}) {
  const m = {
    pipeline: '2026.11-a',
    seed: 7,
    sha256: '',
    width: 1024,
    height: 1024,
    source: { provider: 'opengeodata.goteborg.se orto_2025', version: 'osm 2026-11-15T00:00:00Z; ortho orto_2025' },
    gpu: 'RTX 4090',
    renderedAt: '2026-11-17T11:00:00Z',
    check: 'ok',
    ...overrides,
  };
  return btoa(JSON.stringify(m));
}
