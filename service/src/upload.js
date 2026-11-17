/* The upload: one PNG per cell into the inbox bucket, with its manifest beside it. The Worker
   checks ownership, size, manifest, PNG shape and hash before anything is written, so a
   refused upload leaves no object behind and the cell stays locked. */

import { requireAdmin, requireUser } from './auth.js';
import { logEvent } from './cells.js';
import { error, json } from './db.js';

export const MAX_UPLOAD = 8388608;
export const ALLOWED_SIDES = [1024, 1536];
const DAILY_UPLOADS_NEW = 20;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const MANIFEST_KEYS = ['pipeline', 'sha256', 'width', 'height', 'source', 'gpu', 'renderedAt'];

/**
 * @typedef {{ pipeline: string, seed: number|null, sha256: string, width: number, height: number,
 *   source: { provider: string, version: string }, gpu: string, renderedAt: string }} Manifest
 */

/**
 * Decodes the X-Manifest header: base64 JSON with every key but seed required.
 * @param {string|null} header
 * @returns {Manifest|null}
 */
export function parseManifest(header) {
  if (!header) return null;
  let parsed;
  try {
    parsed = JSON.parse(atob(header));
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  for (const key of MANIFEST_KEYS) if (!(key in parsed)) return null;
  if (typeof parsed.pipeline !== 'string' || typeof parsed.sha256 !== 'string') return null;
  if (!Number.isInteger(parsed.width) || !Number.isInteger(parsed.height)) return null;
  if (!parsed.source || typeof parsed.source.provider !== 'string' || typeof parsed.source.version !== 'string') return null;
  if (typeof parsed.gpu !== 'string' || typeof parsed.renderedAt !== 'string') return null;
  return { ...parsed, seed: Number.isInteger(parsed.seed) ? parsed.seed : null };
}

/**
 * Width and height from a PNG's IHDR, or null when the bytes are not a PNG.
 * @param {Uint8Array} bytes
 * @returns {{ width: number, height: number }|null}
 */
export function inspectPng(bytes) {
  if (bytes.length < 24) return null;
  for (let i = 0; i < 8; i += 1) if (bytes[i] !== PNG_SIGNATURE[i]) return null;
  if (bytes[12] !== 0x49 || bytes[13] !== 0x48 || bytes[14] !== 0x44 || bytes[15] !== 0x52) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

/**
 * Hex sha256 of bytes.
 * @param {ArrayBuffer|Uint8Array} bytes
 * @returns {Promise<string>}
 */
export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Uploads in the last 24 hours by this user.
 * @param {D1Database} db
 * @param {number} githubId
 * @param {string} now
 * @returns {Promise<number>}
 */
async function uploadsLastDay(db, githubId, now) {
  const since = new Date(Date.parse(now) - 24 * 3600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  const row = await db.prepare("SELECT COUNT(*) AS n FROM events WHERE github_id = ? AND action = 'upload' AND at >= ?").bind(githubId, since).first();
  return row.n;
}

/** PUT /v1/upload/:sheet/:col_:row */
export const uploadRoute = requireUser(async (request, env, ctx, params, now, user) => {
  const sheet = params.sheet;
  const col = Number(params.col);
  const row = Number(params.row);
  const cell = await env.DB.prepare('SELECT state, owner_id FROM cells WHERE sheet = ? AND col = ? AND row = ?').bind(sheet, col, row).first();
  if (!cell) return error(404, 'no_cell', 'No such cell.');
  if (cell.owner_id !== user.github_id || cell.state !== 'last') return error(403, 'not_yours', 'You do not hold a lock on that cell.');
  const declared = Number(request.headers.get('Content-Length'));
  if (!Number.isInteger(declared) || declared <= 0) return error(411, 'no_length', 'Send Content-Length.');
  if (declared > MAX_UPLOAD) return error(413, 'too_big', `A cell is at most ${MAX_UPLOAD} bytes.`);
  const manifest = parseManifest(request.headers.get('X-Manifest'));
  if (!manifest) return error(400, 'bad_manifest', 'X-Manifest must be base64 JSON with pipeline, sha256, width, height, source, gpu and renderedAt.');
  const sheetRow = await env.DB.prepare('SELECT pipeline FROM sheets WHERE id = ?').bind(sheet).first();
  if (!sheetRow) return error(404, 'no_sheet', 'No such sheet.');
  if (manifest.pipeline !== sheetRow.pipeline) {
    return error(422, 'wrong_pipeline', `This sheet is drawn with pipeline ${sheetRow.pipeline}; your manifest says ${manifest.pipeline}. Update the template.`);
  }
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.length !== declared) return error(400, 'short_body', `Content-Length said ${declared} bytes, ${bytes.length} arrived.`);
  const png = inspectPng(bytes);
  if (!png) return error(415, 'not_png', 'The body is not a PNG.');
  if (png.width !== png.height || !ALLOWED_SIDES.includes(png.width) || png.width !== manifest.width || png.height !== manifest.height) {
    return error(422, 'bad_size', `A cell is a square of ${ALLOWED_SIDES.join(' or ')} px and the manifest must say so.`);
  }
  const sha = await sha256Hex(bytes);
  if (sha !== manifest.sha256) return error(422, 'sha_mismatch', 'The file does not match the sha256 in the manifest.');
  if (!user.trusted && (await uploadsLastDay(env.DB, user.github_id, now)) >= DAILY_UPLOADS_NEW) {
    return error(429, 'daily_limit', `New builders may upload ${DAILY_UPLOADS_NEW} cells a day. Try again tomorrow.`, { 'Retry-After': '3600' });
  }
  const key = `${sheet}/${col}_${row}`;
  await env.INKORG.put(`${key}.png`, bytes, { httpMetadata: { contentType: 'image/png' } });
  await env.INKORG.put(`${key}.json`, JSON.stringify({ ...manifest, sheet, col, row, login: user.login, github_id: user.github_id, uploadedAt: now }), {
    httpMetadata: { contentType: 'application/json' },
  });
  const state = user.trusted ? 'godkand' : 'vantar';
  await env.DB.prepare('UPDATE cells SET state = ?, uploaded_at = ?, bytes = ?, sha256 = ?, approved_at = ?, updated_at = ? WHERE sheet = ? AND col = ? AND row = ?')
    .bind(state, now, bytes.length, sha, user.trusted ? now : null, now, sheet, col, row)
    .run();
  await logEvent(env.DB, { at: now, github_id: user.github_id, login: user.login, action: 'upload', sheet, col, row, detail: `${bytes.length} bytes, ${png.width} px, ${state}` });
  return json(201, { state, url: `https://karta.opensverige.se/pixelbygget#${key}` });
});

/** GET /v1/inkorg/:sheet/:col_:row.(png|json), admin only. */
export const inkorgRoute = requireAdmin(async (request, env, ctx, params) => {
  const key = `${params.sheet}/${params.col}_${params.row}.${params.ext}`;
  const object = await env.INKORG.get(key);
  if (!object) return error(404, 'not_found', 'Nothing in the inbox under that name.');
  const headers = new Headers();
  headers.set('Content-Type', object.httpMetadata?.contentType || 'application/octet-stream');
  headers.set('Cache-Control', 'private, no-store');
  return new Response(object.body, { status: 200, headers });
});
