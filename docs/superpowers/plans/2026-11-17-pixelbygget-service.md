# Pixelbygget service (rut-tjänsten) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Cloudflare Worker that locks grid cells, accepts cell uploads into an inbox bucket, serves the grid, and lets the admin review, so the CLI, the intake and the build page have one source of truth.

**Architecture:** One Worker (`pixelbygget`) in plain JavaScript modules, no framework, mirroring `pixelkarta-worker`. State in D1 (`sheets`, `cells`, `users`, `events`), uploads in the R2 bucket `pixelbygget-inkorg` through the binding, GitHub user lookups cached in KV. Auth is a GitHub user token passed as Bearer; the Worker holds no secret but the admin id. A cron trigger expires locks and purges rejected uploads.

**Tech Stack:** Cloudflare Workers (compatibility_date 2026-11-01), D1, R2, KV, ratelimits binding, cron trigger; Vitest with `@cloudflare/vitest-pool-workers` for tests; wrangler 4.163.0 via npx; Node 22.

**Spec:** `docs/superpowers/specs/2026-11-17-pixelbygget-design.md`, sections 5, 6, 7, 9b, 13. This plan covers the service only. The CLI, the intake and the build page get their own plans.

## Global Constraints

- No new runtime dependencies. Dev dependencies `vitest`, `@cloudflare/vitest-pool-workers` and `wrangler` need Gustaf's ok before the first `npm install` (asked in the hand-off).
- Code identifiers and commit messages in English; HTTP routes, state names and user-facing strings as the spec writes them (`fri`, `last`, `vantar`, `godkand`, `live`, `ute`).
- The unit is a sheet (`goteborg-norr`), never a city. A cell id is `<col>_<row>`, zero-indexed from the sheet's top-left; a cell is 1024 px.
- Lock lifetime 48 hours. New users: 1 lock, 20 uploads per day. Trusted (10 approved): 8 locks, no daily limit.
- Upload: max 8 388 608 bytes, `image/png`, square 1024 or 1536 px, manifest in header `X-Manifest` (base64 JSON), manifest `pipeline` must equal the sheet's `pipeline`.
- Rate limit 60 requests per minute per IP (ratelimits binding `TAKT`). Kill switch `PIXELBYGGET_STANGD="1"` → 503 on everything but GET.
- Admin = request user whose GitHub id equals var `ADMIN_GITHUB_ID`. No other roles.
- No IP addresses stored in D1. Users table holds GitHub id and login only.
- CORS: `GET` responses allow origins listed in var `ALLOWED_ORIGINS` (comma separated); other methods are CLI calls and get no CORS headers.
- Explicit types in JSDoc on every exported function; no `any`-style `Object` params without shape comments; no TODO comments; no emojis.

## Review Focus

- A second claim on the same cell in the same instant must lose: both callers get a definite answer, never two 201s (Task 5 test `claim_race_one_wins`).
- An upload whose body is shorter than its `Content-Length`, or not a PNG, leaves no object behind and the cell stays `last` (Task 6 test `upload_truncated_leaves_cell_locked`).
- A token GitHub rejects (401) must never be cached as a user, and a GitHub outage (5xx) must read as "try later", not as "not logged in" (Task 3 tests `resolve_rejected_token_not_cached`, `resolve_github_down_is_503`).
- An expired lock whose owner uploads one second late: the cron must not free a cell that has an object in the inbox (Task 8 test `expire_skips_uploaded_cells`).
- A sheet opened twice, or a `seed` naming a cell outside the mask, must be refused whole, not half applied (Task 9 tests `open_sheet_twice_is_409`, `seed_outside_mask_is_422_and_nothing_written`).

---

## File structure

All under `service/` in the repo (today `karta.opensverige-3d`, to become `opensverige/pixelbygget`):

- `service/wrangler.jsonc`: name `pixelbygget`, main `src/index.js`, bindings `DB` (D1 `pixelbygget`), `INKORG` (R2 `pixelbygget-inkorg`), `GH` (KV), `TAKT` (ratelimits 60/60), vars `ADMIN_GITHUB_ID`, `PIXELBYGGET_STANGD`, `ALLOWED_ORIGINS`, `triggers.crons: ["0 * * * *"]`.
- `service/package.json`, `service/vitest.config.js`: test runner only.
- `service/migrations/0001_init.sql`: schema.
- `service/src/index.js`: `fetch` and `scheduled` entry, router, kill switch, rate limit, CORS, JSON helpers.
- `service/src/auth.js`: GitHub token → user.
- `service/src/db.js`: typed query helpers used by every module.
- `service/src/sheets.js`: list sheets, grid, open sheet, seed.
- `service/src/cells.js`: claim, release, limits, events.
- `service/src/upload.js`: manifest parsing, PNG inspection, upload handler, inbox read.
- `service/src/admin.js`: approve, reject, trust, block, queue, live.
- `service/src/cron.js`: expire locks, purge rejected.
- `service/test/*.test.js`: one file per module plus `router.test.js`.
- `service/README.md`: deploy, bindings to create, smoke test.

Shared shapes (JSDoc typedefs in `src/db.js`):

```js
/** @typedef {{ id: string, city: string, name: string, cols: number, rows: number,
 *   origin_lat: number, origin_lon: number, x_meters: number, y_meters: number,
 *   ground_height: number, dzi_levels: number, pipeline: string,
 *   state: 'queued'|'open'|'done', queue_order: number, rev: number }} Sheet */
/** @typedef {{ sheet: string, col: number, row: number,
 *   state: 'ute'|'fri'|'last'|'vantar'|'godkand'|'live',
 *   owner_id: number|null, owner_login: string|null, locked_at: string|null,
 *   expires_at: string|null, uploaded_at: string|null, approved_at: string|null,
 *   live_at: string|null, rejected_at: string|null, bytes: number|null,
 *   sha256: string|null, comment: string|null, updated_at: string }} Cell */
/** @typedef {{ github_id: number, login: string, first_seen: string,
 *   approved_count: number, trusted: 0|1, blocked: 0|1 }} User */
```

Timestamps are ISO 8601 UTC strings produced by `nowIso()` in `db.js`; every handler receives `now` so tests can pin time.

---

### Task 1: Scaffold the Worker and the schema

**Files:**
- Create: `service/wrangler.jsonc`, `service/package.json`, `service/vitest.config.js`, `service/migrations/0001_init.sql`, `service/src/index.js`, `service/src/db.js`, `service/test/router.test.js`, `service/.gitignore` (`node_modules/`, `.wrangler/`)

**Interfaces:**
- Produces: `export default { fetch(request, env, ctx), scheduled(event, env, ctx) }` in `index.js`; `json(status: number, body: object, extra?: HeadersInit): Response`, `error(status: number, code: string, message: string): Response` (body `{error: code, message}`), `nowIso(d?: Date): string` in `db.js`; the schema below.

Schema (`0001_init.sql`): tables `sheets` (columns of the `Sheet` typedef, `id TEXT PRIMARY KEY`), `cells` (typedef columns, `PRIMARY KEY (sheet, col, row)`, `INDEX cells_state ON cells(sheet, state)`, `INDEX cells_owner ON cells(owner_id, state)`), `users` (`github_id INTEGER PRIMARY KEY`), `events` (`id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, github_id INTEGER, login TEXT, action TEXT, sheet TEXT, col INTEGER, row INTEGER, detail TEXT`, `INDEX events_at ON events(at)`).

- [ ] **Step 1: Write the failing test** in `test/router.test.js`:

```js
import { SELF } from 'cloudflare:test';
it('health answers json', async () => {
  const r = await SELF.fetch('https://x/v1/health');
  expect(r.status).toBe(200);
  expect(await r.json()).toEqual({ ok: true, service: 'pixelbygget' });
});
it('unknown path is 404 json', async () => {
  const r = await SELF.fetch('https://x/v1/nope');
  expect(r.status).toBe(404);
  expect((await r.json()).error).toBe('not_found');
});
```

- [ ] **Step 2: Run** `cd service && npx vitest run test/router.test.js` → FAIL (no worker entry).
- [ ] **Step 3: Write `wrangler.jsonc`** with the bindings in File structure, `vitest.config.js` using `defineWorkersConfig` with `wrangler: { configPath: './wrangler.jsonc' }` and `miniflare.d1Databases`/`r2Buckets`/`kvNamespaces` matching the binding names, and `index.js` with a router: `const routes = [[method, /^\/v1\/path(\/...)?$/, handler]]`, dispatching to handlers that receive `(request, env, ctx, params, now)`.
- [ ] **Step 4: Run** the test → PASS.
- [ ] **Step 5: Apply the migration in tests**: add `test/setup.js` that runs `applyD1Migrations(env.DB, migrations)` via `cloudflare:test`, and a test `schema has four tables` asserting `sqlite_master` names. Run → PASS.
- [ ] **Step 6: Commit** `feat(service): scaffold pixelbygget worker and schema`.

### Task 2: Kill switch, rate limit, CORS

**Files:**
- Modify: `service/src/index.js`
- Test: `service/test/router.test.js`

**Interfaces:**
- Produces: `withCors(response: Response, request: Request, env: Env): Response`; routing order in `fetch`: kill switch → rate limit → router → cors.

- [ ] **Step 1: Write failing tests**: `kill switch gives 503 on POST and GET passes` (set `env.PIXELBYGGET_STANGD = '1'` via `vi.stubEnv`-style env override in the test harness, POST `/v1/claim` → 503 with `Retry-After: 300`, GET `/v1/health` → 200); `cors header only for allowed origin on GET` (Origin `https://karta.opensverige.se` → `Access-Control-Allow-Origin` echoed; Origin `https://evil.example` → header absent; OPTIONS preflight → 204 with `Access-Control-Allow-Methods: GET`); `rate limited is 429` (mock `env.TAKT.limit` to return `{ success: false }` → 429 with `Retry-After: 30`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** in `index.js`: the IP key as in pixelkarta (`CF-Connecting-IP`, IPv6 collapsed to /64), `env.TAKT.limit({ key })`, and `withCors`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): kill switch, rate limit and cors`.

### Task 3: GitHub auth

**Files:**
- Create: `service/src/auth.js`
- Test: `service/test/auth.test.js`

**Interfaces:**
- Produces: `resolveUser(request: Request, env: Env, now: string): Promise<{ user: User, isAdmin: boolean } | Response>` — returns a Response (401 `no_token`, 401 `bad_token`, 403 `blocked`, 503 `github_down`) when the caller must stop; `requireUser(handler)` wrapper that resolves and injects `user` as a sixth handler argument; `requireAdmin(handler)` likewise, 403 `not_admin`.
- GitHub call: `GET https://api.github.com/user` with `Authorization: Bearer <token>`, `User-Agent: pixelbygget`, `Accept: application/vnd.github+json`. Cache key `gh:` + hex sha256 of the token, value `{ id, login }`, TTL 600 s.

- [ ] **Step 1: Write failing tests** with `fetchMock` from `cloudflare:test`: `resolve_caches_user_for_600s` (second call makes no GitHub request; KV holds the key with `expirationTtl` 600), `resolve_rejected_token_not_cached` (GitHub 401 → 401 `bad_token`, KV empty), `resolve_github_down_is_503` (GitHub 502 → 503 `github_down`), `resolve_upserts_user_row` (first sight inserts `users` row with `first_seen = now`), `resolve_blocked_is_403`, `admin_is_recognised` (`ADMIN_GITHUB_ID` equals id → `isAdmin: true`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `auth.js` and route `GET /v1/me` returning `{ login, github_id, trusted, approved_count, locks: Cell[] }` (locks = the user's cells in state `last`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): github token auth with kv cache`.

### Task 4: Sheets and grid

**Files:**
- Create: `service/src/sheets.js`
- Test: `service/test/sheets.test.js`, `service/test/fixtures.js` (helper `insertSheet(db, overrides)`, `insertCells(db, sheet, cols, rows, maskFn)`)

**Interfaces:**
- Produces: `listSheets(db: D1Database): Promise<Array<Sheet & { done: number, total: number }>>` (`total` = cells not `ute`, `done` = cells `live`); `getGrid(db, sheetId): Promise<{ sheet: Sheet, cells: Array<[number, number, string, string|null, string]> } | null>` (rows `[col, row, state, owner_login, updated_at]`, `ute` cells included so the client can draw them).
- Routes: `GET /v1/sheets`, `GET /v1/cities` (sheets grouped: `{ cities: [{ id, name, sheets: [...], done, total }] }`), `GET /v1/grid/:sheet` (404 `no_sheet`). GET responses carry `Cache-Control: public, max-age=60`.

- [ ] **Step 1: Write failing tests**: `list_counts_done_and_total_excluding_ute`, `grid_rows_are_compact_arrays`, `grid_unknown_sheet_404`, `cities_group_sheets_by_city`, `grid_has_60s_cache_header`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with two queries (`GROUP BY state` for counts; one `SELECT col,row,state,owner_login,updated_at ORDER BY row,col` for the grid).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): sheets, cities and grid endpoints`.

### Task 5: Claim and release

**Files:**
- Create: `service/src/cells.js`
- Test: `service/test/cells.test.js`

**Interfaces:**
- Produces: `claimCell(db, user: User, sheet: string, col: number, row: number, now: string): Promise<{ ok: true, expires_at: string } | { ok: false, status: number, code: 'no_sheet'|'sheet_closed'|'outside'|'taken'|'too_many_locks'|'blocked' }>`; `releaseCell(db, user, sheet, col, row, now): Promise<{ ok: true } | { ok: false, status, code: 'not_yours'|'already_uploaded'|'no_cell' }>`; `logEvent(db, { at, github_id, login, action, sheet, col, row, detail })`; `lockLimit(user: User): number` (1 or 8); `LOCK_HOURS = 48`.
- Routes: `POST /v1/claim` body `{ sheet, col, row }` → 201 `{ sheet, col, row, expires_at }`; `POST /v1/release` → 200 `{ ok: true }`. Body validation: `col`, `row` integers ≥ 0, else 400 `bad_body`.
- The claim is one conditional `UPDATE cells SET state='last', owner_id=?, owner_login=?, locked_at=?, expires_at=?, updated_at=? WHERE sheet=? AND col=? AND row=? AND state='fri'`; success iff `meta.changes === 1`. The lock-count check runs before it and the update is still the only arbiter.

- [ ] **Step 1: Write failing tests**: `claim_free_cell_locks_48h` (expires_at = now + 48 h), `claim_taken_is_409`, `claim_ute_is_422`, `claim_closed_sheet_is_403`, `claim_over_limit_is_403_new_user_1_trusted_8`, `claim_race_one_wins` (`Promise.all` of two claims by two users → exactly one 201), `release_frees_own_lock`, `release_other_users_lock_is_403`, `release_after_upload_is_409`, `claim_logs_event`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `cells.js` and the two routes with `requireUser`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): claim and release with conditional lock`.

### Task 6: Upload into the inbox

**Files:**
- Create: `service/src/upload.js`
- Test: `service/test/upload.test.js`, `service/test/png.js` (helper `minimalPng(width, height): Uint8Array` building signature + IHDR + IEND)

**Interfaces:**
- Produces: `parseManifest(header: string|null): { pipeline: string, seed: number|null, sha256: string, width: number, height: number, source: { provider: string, version: string }, gpu: string, renderedAt: string } | null` (base64 JSON, all keys required but `seed`); `inspectPng(bytes: Uint8Array): { width: number, height: number } | null` (signature + IHDR at bytes 16–23, big-endian); `sha256Hex(bytes: ArrayBuffer): Promise<string>`; `MAX_UPLOAD = 8388608`; `ALLOWED_SIDES = [1024, 1536]`.
- Route: `PUT /v1/upload/:sheet/:col_:row` with `Content-Type: image/png`, `Content-Length`, `X-Manifest`. Order of checks: user owns the lock (403 `not_yours`), `Content-Length` ≤ max (413 `too_big`), manifest parses (400 `bad_manifest`), manifest pipeline equals sheet pipeline (422 `wrong_pipeline`), body read fully and length equals `Content-Length` (400 `short_body`), PNG with allowed square side matching manifest (415 `not_png` / 422 `bad_size`), sha256 equals manifest (422 `sha_mismatch`), daily limit for new users (429 `daily_limit`, counted from `events` where `action='upload'` since `now` minus 24 h). Then `INKORG.put('<sheet>/<col>_<row>.png', bytes, { httpMetadata: { contentType: 'image/png' } })` and `.json` beside it, cell → `vantar` (or `godkand` if trusted) with `uploaded_at`, `bytes`, `sha256`; event `upload`. Response 201 `{ state, url: 'https://karta.opensverige.se/pixelbygget#<sheet>/<col>_<row>' }`.
- `GET /v1/inkorg/:sheet/:col_:row(.png|.json)` admin only → the object or 404.

- [ ] **Step 1: Write failing tests**: `manifest_roundtrip`, `inspect_png_reads_ihdr`, `upload_ok_writes_png_and_json_and_sets_vantar`, `upload_trusted_goes_straight_to_godkand`, `upload_not_owner_is_403_and_nothing_written`, `upload_wrong_pipeline_is_422`, `upload_truncated_leaves_cell_locked` (Content-Length 100, body 50 bytes → 400, no object, state `last`), `upload_bad_sha_is_422`, `upload_over_8mb_is_413_before_reading` (Content-Length 8388609 → 413), `upload_daily_limit_new_user_20`, `inkorg_get_requires_admin`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `upload.js` and the routes. Read the body with `await request.arrayBuffer()` after the length check.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): cell upload into inbox with manifest and png checks`.

### Task 7: Admin review

**Files:**
- Create: `service/src/admin.js`
- Test: `service/test/admin.test.js`

**Interfaces:**
- Produces: `approveCell(db, sheet, col, row, now)` (`vantar` → `godkand`, `approved_at`, owner's `approved_count += 1`, `trusted = 1` when it reaches 10; 409 `not_waiting` otherwise); `rejectCell(db, inkorg, sheet, col, row, comment: string, now)` (`vantar`|`godkand`|`live` → `fri`, `rejected_at`, `comment`, owner cleared, inbox `.png` and `.json` deleted unless the cell was `live` — then the objects are kept and `detail` says `live-removed` so the intake writes blank tiles); `setTrust(db, login, trusted: 0|1)`; `setBlock(db, login, blocked: 0|1)`; `listQueue(db, state: 'vantar'|'godkand'): Promise<Array<Cell & { neighbours: Array<[number, number, string]> }>>` (the eight neighbours' `[col,row,state]`); `markLive(db, sheet, cells: Array<[number, number]>, rev: number, now)` (`godkand` → `live`, `live_at`, sheet `rev` updated; 409 `not_approved` if any cell is not `godkand`, nothing written).
- Routes, all `requireAdmin`: `POST /v1/approve {sheet,col,row}`, `POST /v1/reject {sheet,col,row,comment}` (comment required, ≤ 500 chars), `POST /v1/trust {login, trusted}`, `POST /v1/block {login, blocked}`, `GET /v1/queue?state=vantar`, `POST /v1/live {sheet, cells, rev}`.

- [ ] **Step 1: Write failing tests**: `approve_sets_godkand_and_counts`, `tenth_approval_makes_trusted`, `approve_non_waiting_is_409`, `reject_frees_cell_and_deletes_inbox`, `reject_live_keeps_inbox_and_logs_live_removed`, `queue_lists_waiting_with_neighbours`, `live_marks_all_or_nothing`, `admin_routes_refuse_non_admin`, `every_admin_action_logs_event`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `admin.js`; `markLive` uses `db.batch([...])` so it is atomic.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): admin review, trust and live marking`.

### Task 8: Cron

**Files:**
- Create: `service/src/cron.js`
- Modify: `service/src/index.js` (`scheduled`)
- Test: `service/test/cron.test.js`

**Interfaces:**
- Produces: `expireLocks(db, now): Promise<number>` (cells `last` with `expires_at < now` and `uploaded_at IS NULL` → `fri`, owner cleared, event `expired` per cell; returns count); `purgeRejected(db, inkorg, now): Promise<number>` (cells with `rejected_at < now − 30 d` whose inbox objects still exist → delete objects, clear `rejected_at`).

- [ ] **Step 1: Write failing tests**: `expire_frees_old_locks_only`, `expire_skips_uploaded_cells`, `purge_deletes_objects_after_30_days`, `scheduled_runs_both_and_logs_counts` (console spy, or an event row `action='cron'` with `detail='expired=1 purged=0'`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** and wire `scheduled(event, env, ctx)`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): hourly lock expiry and inbox purge`.

### Task 9: Open a sheet and seed

**Files:**
- Modify: `service/src/sheets.js`
- Test: `service/test/sheets.test.js`

**Interfaces:**
- Produces: `openSheet(db, body: { id, city, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, pipeline, queue_order, state, mask: Array<[number, number]> }, now)` — inserts the sheet and `cols × rows` cells (`fri` for cells in `mask`, `ute` otherwise) in batches of 500 statements; 409 `exists` if the id exists, 400 `bad_body` with the failing field name if any field is missing or `mask` has a cell outside `[0,cols)×[0,rows)`. Nothing is written on refusal (validate first, then `db.batch`).
- `seedCells(db, sheet, cells: Array<[number, number]>, login: string, github_id: number, now)` — every listed cell must be `fri` (422 `seed_outside_mask` naming the first offender, nothing written); sets `live`, `owner_login`, `owner_id`, `live_at`, `approved_at`.
- Routes `POST /v1/sheets`, `POST /v1/seed {sheet, cells, login, github_id}`, both admin.

- [ ] **Step 1: Write failing tests**: `open_sheet_writes_fri_and_ute`, `open_sheet_twice_is_409`, `open_sheet_bad_mask_is_400_and_nothing_written`, `seed_marks_live_credited`, `seed_outside_mask_is_422_and_nothing_written`, `open_sheet_15340_cells_within_batch_limits` (130×118, assert count).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(service): open sheet and seed existing cells`.

### Task 10: README, smoke test, deploy notes

**Files:**
- Create: `service/README.md`, `service/scripts/smoke.sh`

- [ ] **Step 1: Write `README.md`**: what the service is (two paragraphs from the spec, sections 5 and 7), the bindings Gustaf creates once (`npx wrangler d1 create pixelbygget`, `npx wrangler r2 bucket create pixelbygget-inkorg --location weur`, `npx wrangler kv namespace create GH`, the GitHub OAuth App with device flow and its client id for the CLI, `ADMIN_GITHUB_ID`), `npx wrangler d1 migrations apply pixelbygget --remote`, deploy with `npx wrangler@4.163.0 deploy`, test with `npx vitest run`, the kill switch, and the endpoint table (method, path, auth, codes).
- [ ] **Step 2: Write `scripts/smoke.sh`**: `curl` against a `BASE` url: health, sheets, grid of the first sheet, `/me` with `GH_TOKEN`, claim of a cell given on the command line, upload of a generated 1024 px PNG (python one-liner building the PNG and manifest), release. Exits non-zero on the first unexpected status.
- [ ] **Step 3: Run** `npx vitest run` → all green; `npx wrangler deploy --dry-run` → bindings listed.
- [ ] **Step 4: Commit** `docs(service): readme and smoke test`.
