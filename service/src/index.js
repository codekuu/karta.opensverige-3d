/* The Worker pixelbygget: rut-tjänsten.

   Routes live under /v1. Each handler is called as
   handler(request, env, ctx, params, now) and returns a Response. The order in fetch is:
   kill switch, rate limit, router, cors. See docs/pixelbygget/DESIGN.md for the rules.

   Kill switch: PIXELBYGGET_STANGD="1" answers 503 to everything but GET, so the grid stays
   readable while uploads and claims are paused.

   Rate: TAKT requests per minute per client, counted per Cloudflare location, as in
   pixelkarta. The key is the client address, IPv6 as its /64. Without the binding or when the
   limiter fails the answer is "not over": the rate must never take the service down.

   The admin passes the rate limit on admin routes when its token is already in the KV cache,
   so the intake can fetch a long queue. The cache is only read for a request already over the
   limit. Everyone else, admin routes included, is counted.

   Public reads (/v1/sheets, /v1/cities, /v1/grid/*) are kept in memory per isolate for
   PUBLIC_CACHE_SECONDS (default 60), so a busy map page does not read every cell from D1 on
   every hit. The Cache API does not work on workers.dev, so this is the cache we have.
   /v1/place is not kept: every point is its own answer, and a stream of them would push
   the grids out of the 64 places. It reads four rows at most.

   CORS: GET answers carry Access-Control-Allow-Origin for an origin in ALLOWED_ORIGINS, so
   the build page can read the grid. Other methods are CLI calls and get no CORS headers. */

import { approveRoute, blankedRoute, blockRoute, liveRoute, queueRoute, rejectRoute, trustRoute } from './admin.js';
import { isCachedAdmin, requireUser } from './auth.js';
import { error, json, nowIso } from './db.js';
import { claimRoute, releaseRoute } from './cells.js';
import { runCron } from './cron.js';
import { citiesRoute, gridRoute, openSheetRoute, placeRoute, seedRoute, sheetsRoute } from './sheets.js';
import { inkorgRoute, uploadRoute } from './upload.js';

/**
 * GET /v1/me: the caller, their counts and their locks.
 * @type {Function}
 */
const me = requireUser(async (request, env, ctx, params, now, user) => {
  const { results } = await env.DB.prepare(
    "SELECT * FROM cells WHERE owner_id = ? AND state = 'last' ORDER BY locked_at",
  )
    .bind(user.github_id)
    .all();
  return json(200, {
    login: user.login,
    github_id: user.github_id,
    trusted: user.trusted,
    approved_count: user.approved_count,
    locks: results,
  });
});

/** @type {Array<[string, RegExp, Function]>} */
const routes = [
  ['GET', /^\/v1\/health$/, () => json(200, { ok: true, service: 'pixelbygget' })],
  ['GET', /^\/v1\/me$/, me],
  ['GET', /^\/v1\/sheets$/, sheetsRoute],
  ['GET', /^\/v1\/cities$/, citiesRoute],
  ['GET', /^\/v1\/grid\/(?<sheet>[a-z0-9-]+)$/, gridRoute],
  ['GET', /^\/v1\/place$/, placeRoute],
  ['POST', /^\/v1\/claim$/, claimRoute],
  ['POST', /^\/v1\/release$/, releaseRoute],
  ['PUT', /^\/v1\/upload\/(?<sheet>[a-z0-9-]+)\/(?<col>\d+)_(?<row>\d+)$/, uploadRoute],
  ['GET', /^\/v1\/inkorg\/(?<sheet>[a-z0-9-]+)\/(?<col>\d+)_(?<row>\d+)\.(?<ext>png|json)$/, inkorgRoute],
  ['POST', /^\/v1\/approve$/, approveRoute],
  ['POST', /^\/v1\/reject$/, rejectRoute],
  ['POST', /^\/v1\/trust$/, trustRoute],
  ['POST', /^\/v1\/block$/, blockRoute],
  ['GET', /^\/v1\/queue$/, queueRoute],
  ['POST', /^\/v1\/live$/, liveRoute],
  ['POST', /^\/v1\/blanked$/, blankedRoute],
  ['POST', /^\/v1\/sheets$/, openSheetRoute],
  ['POST', /^\/v1\/seed$/, seedRoute],
];

const ADMIN_PATH = /^\/v1\/(queue|inkorg\/.+|approve|reject|trust|block|live|blanked|seed)$/;
const PUBLIC_READ = /^\/v1\/(sheets|cities|grid\/[a-z0-9-]+)$/;
const PUBLIC_CACHE_MAX = 64;
/** @type {Map<string, { until: number, status: number, body: string, headers: [string, string][] }>} */
const publicCache = new Map();

/** Empties the in-memory cache of public reads. For tests. */
export function clearPublicCache() {
  publicCache.clear();
}

/**
 * Seconds a public read is kept in memory.
 * @param {import('./db.js').Env} env
 * @returns {number}
 */
function publicSeconds(env) {
  const raw = Number(env.PUBLIC_CACHE_SECONDS ?? 60);
  return Number.isFinite(raw) && raw > 0 ? raw : 0;
}

/**
 * Answers a public read from memory, or runs it and keeps a 200 answer for the window.
 * @param {string} pathname
 * @param {import('./db.js').Env} env
 * @param {() => Promise<Response>} run
 * @returns {Promise<Response>}
 */
async function cachedPublic(pathname, env, run) {
  const seconds = publicSeconds(env);
  if (!seconds) return run();
  const hit = publicCache.get(pathname);
  if (hit && hit.until > Date.now()) return new Response(hit.body, { status: hit.status, headers: hit.headers });
  const response = await run();
  // An answer that says no-store is not kept: the grid of a sheet nobody has made yet. There
  // are thousands of those ids, and the claim that makes the sheet changes the answer.
  if (response.status !== 200 || (response.headers.get('Cache-Control') || '').includes('no-store')) return response;
  const body = await response.text();
  const headers = [...response.headers.entries()];
  if (publicCache.size >= PUBLIC_CACHE_MAX) publicCache.delete(publicCache.keys().next().value);
  publicCache.set(pathname, { until: Date.now() + seconds * 1000, status: 200, body, headers });
  return new Response(body, { status: 200, headers });
}

/**
 * Finds the first route matching the method and path.
 * @param {string} method
 * @param {string} pathname
 * @returns {{ handler: Function, params: Record<string, string> } | null}
 */
function match(method, pathname) {
  for (const [m, re, handler] of routes) {
    if (m !== method) continue;
    const found = re.exec(pathname);
    if (found) return { handler, params: found.groups || {} };
  }
  return null;
}

/**
 * The client as the rate counts it: an IPv4 address as it is, an IPv6 address as its /64.
 * @param {Request} request
 * @returns {string}
 */
export function clientKey(request) {
  const ip = (request.headers.get('CF-Connecting-IP') || '').trim();
  if (!ip.includes(':')) return ip;
  const [head, tail = ''] = ip.split('::');
  const front = head ? head.split(':') : [];
  const back = tail ? tail.split(':') : [];
  const groups = [...front, ...new Array(Math.max(0, 8 - front.length - back.length)).fill('0'), ...back];
  return groups.slice(0, 4).map((g) => g.padStart(4, '0')).join(':') + '::/64';
}

/**
 * True when this client has used up its minute.
 * @param {import('./db.js').Env} env
 * @param {string} key
 * @returns {Promise<boolean>}
 */
async function overRate(env, key) {
  if (!env.TAKT || !key) return false;
  try {
    const { success } = await env.TAKT.limit({ key });
    return !success;
  } catch (e) {
    console.log(JSON.stringify({ takt: 'fel', fel: String(e) }));
    return false;
  }
}

/**
 * The origins that may read GET endpoints from a browser.
 * @param {import('./db.js').Env} env
 * @returns {Set<string>}
 */
function allowedOrigins(env) {
  return new Set((env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean));
}

/**
 * Adds CORS headers to a GET or OPTIONS answer when the Origin is allowed.
 * @param {Response} response
 * @param {Request} request
 * @param {import('./db.js').Env} env
 * @returns {Response}
 */
export function withCors(response, request, env) {
  if (request.method !== 'GET' && request.method !== 'OPTIONS') return response;
  const origin = request.headers.get('Origin');
  if (!origin || !allowedOrigins(env).has(origin)) return response;
  const headers = new Headers(response.headers);
  headers.set('Access-Control-Allow-Origin', origin);
  headers.set('Access-Control-Allow-Methods', 'GET');
  headers.set('Access-Control-Max-Age', '86400');
  headers.append('Vary', 'Origin');
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

export default {
  /**
   * @param {Request} request
   * @param {import('./db.js').Env} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const now = nowIso();
    if (request.method === 'OPTIONS') {
      return withCors(new Response(null, { status: 204 }), request, env);
    }
    if (env.PIXELBYGGET_STANGD === '1' && request.method !== 'GET') {
      return withCors(error(503, 'closed', 'Pixelbygget is closed for the moment. Try again later.', { 'Retry-After': '300' }), request, env);
    }
    if ((await overRate(env, clientKey(request))) && !(ADMIN_PATH.test(url.pathname) && (await isCachedAdmin(request, env)))) {
      return withCors(error(429, 'rate_limited', 'Too many requests. Wait half a minute.', { 'Retry-After': '30' }), request, env);
    }
    const route = match(request.method, url.pathname);
    let response;
    try {
      if (!route) response = error(404, 'not_found', 'No such route.');
      else if (request.method === 'GET' && PUBLIC_READ.test(url.pathname)) response = await cachedPublic(url.pathname, env, () => route.handler(request, env, ctx, route.params, now));
      else response = await route.handler(request, env, ctx, route.params, now);
    } catch (e) {
      // A storage outage or a dropped upload body ends here, as JSON the CLI can read.
      console.log(JSON.stringify({ fel: String(e), path: url.pathname, method: request.method }));
      response = error(500, 'internal', 'Something broke on our side. Try again in a minute.', { 'Retry-After': '60' });
    }
    return withCors(response, request, env);
  },

  /**
   * @param {ScheduledController} event
   * @param {import('./db.js').Env} env
   * @param {ExecutionContext} ctx
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runCron(env, nowIso()));
  },
};
