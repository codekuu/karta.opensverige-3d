/* The Worker pixelbygget: rut-tjänsten.

   Routes live under /v1. Each handler is called as
   handler(request, env, ctx, params, now) and returns a Response. The order in fetch is:
   kill switch, rate limit, router, cors. See the design document for the rules.

   Kill switch: PIXELBYGGET_STANGD="1" answers 503 to everything but GET, so the grid stays
   readable while uploads and claims are paused.

   Rate: TAKT requests per minute per client, counted per Cloudflare location, as in
   pixelkarta. The key is the client address, IPv6 as its /64. Without the binding or when the
   limiter fails the answer is "not over": the rate must never take the service down.

   CORS: GET answers carry Access-Control-Allow-Origin for an origin in ALLOWED_ORIGINS, so
   the build page can read the grid. Other methods are CLI calls and get no CORS headers. */

import { requireUser } from './auth.js';
import { error, json, nowIso } from './db.js';
import { claimRoute, releaseRoute } from './cells.js';
import { citiesRoute, gridRoute, sheetsRoute } from './sheets.js';
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
  ['POST', /^\/v1\/claim$/, claimRoute],
  ['POST', /^\/v1\/release$/, releaseRoute],
  ['PUT', /^\/v1\/upload\/(?<sheet>[a-z0-9-]+)\/(?<col>\d+)_(?<row>\d+)$/, uploadRoute],
  ['GET', /^\/v1\/inkorg\/(?<sheet>[a-z0-9-]+)\/(?<col>\d+)_(?<row>\d+)\.(?<ext>png|json)$/, inkorgRoute],
];

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
      return error(503, 'closed', 'Pixelbygget is closed for the moment. Try again later.', { 'Retry-After': '300' });
    }
    if (await overRate(env, clientKey(request))) {
      return error(429, 'rate_limited', 'Too many requests. Wait half a minute.', { 'Retry-After': '30' });
    }
    const route = match(request.method, url.pathname);
    const response = route
      ? await route.handler(request, env, ctx, route.params, now)
      : error(404, 'not_found', 'No such route.');
    return withCors(response, request, env);
  },
};
