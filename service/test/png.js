/* A minimal PNG for tests: signature, IHDR, IEND. Not decodable as an image, but it carries
   the width and height the service reads. */

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function u32(n) {
  return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
}

/**
 * @param {number} width
 * @param {number} height
 * @param {number} [padding] extra bytes after IEND, to reach a wanted size
 * @returns {Uint8Array}
 */
export function minimalPng(width, height, padding = 0) {
  const ihdrData = [...u32(width), ...u32(height), 8, 6, 0, 0, 0];
  const ihdr = [...u32(13), 0x49, 0x48, 0x44, 0x52, ...ihdrData, 0, 0, 0, 0];
  const iend = [...u32(0), 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82];
  return new Uint8Array([...SIGNATURE, ...ihdr, ...iend, ...new Array(padding).fill(0)]);
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
 * A manifest for a cell upload, base64 encoded for the X-Manifest header.
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
    source: { provider: 'google-3d-tiles', version: '2026-10' },
    gpu: 'RTX 4090',
    renderedAt: '2026-11-17T11:00:00Z',
    ...overrides,
  };
  return btoa(JSON.stringify(m));
}
