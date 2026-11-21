/* Who is calling. The CLI sends the GitHub user token it got from the device flow as a
   Bearer header; we ask GitHub who it belongs to and cache the answer ten minutes in KV under
   a hash of the token, never the token itself. The service has no sessions of its own. */

import { error, nowIso } from './db.js';

const GITHUB_USER = 'https://api.github.com/user';
const CACHE_TTL = 600;

/**
 * Hex sha256 of a string.
 * @param {string} text
 * @returns {Promise<string>}
 */
async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * The bearer token on a request, or null.
 * @param {Request} request
 * @returns {string|null}
 */
function bearer(request) {
  const header = request.headers.get('Authorization') || '';
  const found = /^Bearer\s+(\S+)$/i.exec(header.trim());
  return found ? found[1] : null;
}

/**
 * Asks GitHub who the token belongs to.
 * @param {string} token
 * @returns {Promise<{ id: number, login: string } | 'bad' | 'down'>}
 */
async function lookupGithub(token) {
  let response;
  try {
    response = await fetch(GITHUB_USER, {
      headers: {
        Authorization: `Bearer ${token}`,
        'User-Agent': 'pixelbygget',
        Accept: 'application/vnd.github+json',
      },
    });
  } catch {
    return 'down';
  }
  if (response.status === 401) return 'bad';
  if (response.status === 403) {
    // GitHub answers 403 both for a token without rights and for its rate limits.
    const remaining = response.headers.get('x-ratelimit-remaining');
    const text = await response.text().catch(() => '');
    if (remaining === '0' || /rate limit/i.test(text)) return 'down';
    return 'bad';
  }
  if (!response.ok) return 'down';
  let body;
  try {
    body = await response.json();
  } catch {
    return 'down';
  }
  if (!body || typeof body.id !== 'number' || typeof body.login !== 'string') return 'down';
  return { id: body.id, login: body.login };
}

/**
 * Resolves the caller to a user row, creating it on first sight.
 * Returns a Response when the caller must stop: 401 no_token, 401 bad_token, 403 blocked,
 * 503 github_down.
 * @param {Request} request
 * @param {import('./db.js').Env} env
 * @param {string} now
 * @returns {Promise<{ user: import('./db.js').User, isAdmin: boolean } | Response>}
 */
export async function resolveUser(request, env, now) {
  const token = bearer(request);
  if (!token) return error(401, 'no_token', 'Log in first: rutor login.');
  const key = 'gh:' + (await sha256Hex(token));
  let identity = await env.GH.get(key, 'json');
  if (!identity) {
    const looked = await lookupGithub(token);
    if (looked === 'bad') return error(401, 'bad_token', 'GitHub does not accept this token. Log in again: rutor login.');
    if (looked === 'down') return error(503, 'github_down', 'GitHub did not answer. Try again in a minute.', { 'Retry-After': '60' });
    identity = looked;
    await env.GH.put(key, JSON.stringify(identity), { expirationTtl: CACHE_TTL });
  }
  await env.DB.prepare(
    'INSERT INTO users (github_id, login, first_seen) VALUES (?, ?, ?) ON CONFLICT(github_id) DO UPDATE SET login = excluded.login',
  )
    .bind(identity.id, identity.login, now)
    .run();
  const user = await env.DB.prepare('SELECT * FROM users WHERE github_id = ?').bind(identity.id).first();
  if (user.blocked) return error(403, 'blocked', 'This account is blocked from Pixelbygget.');
  const isAdmin = String(user.github_id) === String(env.ADMIN_GITHUB_ID || '');
  return { user, isAdmin };
}

/**
 * Wraps a handler so it runs with a resolved user as its sixth argument.
 * @param {(request: Request, env: import('./db.js').Env, ctx: ExecutionContext, params: Record<string, string>, now: string, user: import('./db.js').User, isAdmin: boolean) => Promise<Response>} handler
 */
export function requireUser(handler) {
  return async (request, env, ctx, params, now = nowIso()) => {
    const resolved = await resolveUser(request, env, now);
    if (resolved instanceof Response) return resolved;
    return handler(request, env, ctx, params, now, resolved.user, resolved.isAdmin);
  };
}

/**
 * Like requireUser, but only the admin may pass: 403 not_admin otherwise.
 * @param {(request: Request, env: import('./db.js').Env, ctx: ExecutionContext, params: Record<string, string>, now: string, user: import('./db.js').User) => Promise<Response>} handler
 */
export function requireAdmin(handler) {
  return requireUser(async (request, env, ctx, params, now, user, isAdmin) => {
    if (!isAdmin) return error(403, 'not_admin', 'Only the admin may do this.');
    return handler(request, env, ctx, params, now, user);
  });
}
