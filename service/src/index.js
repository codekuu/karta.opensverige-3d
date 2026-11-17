/* The Worker pixelbygget: rut-tjänsten.

   Routes live under /v1. Each handler is called as
   handler(request, env, ctx, params, now) and returns a Response. The order in fetch is:
   kill switch, rate limit, router, cors. See the design document for the rules. */

import { error, json, nowIso } from './db.js';

/** @type {Array<[string, RegExp, Function]>} */
const routes = [
  ['GET', /^\/v1\/health$/, () => json(200, { ok: true, service: 'pixelbygget' })],
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

export default {
  /**
   * @param {Request} request
   * @param {import('./db.js').Env} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const now = nowIso();
    const route = match(request.method, url.pathname);
    if (!route) return error(404, 'not_found', 'No such route.');
    return route.handler(request, env, ctx, route.params, now);
  },
};
