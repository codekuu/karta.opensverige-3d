# Rut-tjänsten (`pixelbygget`)

The service behind Pixelbygget: one Cloudflare Worker that locks grid cells, takes finished cells into an inbox bucket, serves the grid to the build page and the CLI, and lets the admin review. It is the single source of truth for who holds which cell. The design is in [docs/pixelbygget/DESIGN.md](../docs/pixelbygget/DESIGN.md).

State lives in D1 (`sheets`, `cells`, `users`, `events`, `purge`), uploads in the R2 bucket `pixelbygget-inkorg`, GitHub user lookups in KV for ten minutes. The caller sends the GitHub user token from the device flow as a Bearer header; the service asks GitHub who it is and keeps no sessions. With the secret `GITHUB_CLIENT_SECRET` set (the "Pixelbygget" OAuth app's client secret, beside the variable `GITHUB_CLIENT_ID`), GitHub is asked through the app's own token check, so only a token from `rutor login` is accepted. Without it any GitHub token proves an identity here, one from another site's "Sign in with GitHub" too: set it in production (`npx wrangler secret put GITHUB_CLIENT_SECRET`). The one admin is the GitHub id in `ADMIN_GITHUB_ID`. Each upload is stored under its own inbox key, the cell plus the first 16 hex of its sha256 and 8 random hex, and the cell records that key. A cron runs every hour: locks past their 48 hours go back to free, and rejected uploads whose 30 days are up are deleted from the `purge` list. A live cell the admin removes is marked `removed` until the intake reports it blanked (`POST /blanked`), which also drops its objects; it cannot be claimed before that. Public reads are kept in memory per isolate for `PUBLIC_CACHE_SECONDS` (60 by default).

## Create once (the maintainer)

```bash
npx wrangler@4.163.0 d1 create pixelbygget
```

```bash
npx wrangler@4.163.0 r2 bucket create pixelbygget-inkorg --location weur
```

```bash
npx wrangler@4.163.0 kv namespace create GH
```

Put the printed `database_id` and KV `id` into `wrangler.jsonc`. Then set the admin: your GitHub id (`gh api user --jq .id`) as `ADMIN_GITHUB_ID` in `wrangler.jsonc` or under Settings → Variables.

The CLI needs a GitHub OAuth App named "Pixelbygget" under the organisation with "Enable Device Flow" ticked; its client id is `GITHUB_CLIENT_ID` in `cli/rutor/config.py`, and the same value is the variable `GITHUB_CLIENT_ID` in `wrangler.jsonc` (a test in `cli/tests` holds them together).

## Deploy

Node 22 or newer, logged in with `npx wrangler login`.

```bash
npx wrangler@4.163.0 d1 migrations apply pixelbygget --remote
```

```bash
npx wrangler@4.163.0 deploy
```

`npx wrangler@4.163.0 deploy --dry-run` builds and lists the bindings without uploading.

## Test

Node 22 or newer.

```bash
npm ci
```

```bash
npm test
```

Runs the suite in the Workers runtime with a fresh D1, R2 and KV per file. GitHub is stubbed.

Against a deployed service, `scripts/smoke.sh` walks health, sheets, grid, `/me`, claim, upload and release:

```bash
BASE=https://pixelbygget.<account>.workers.dev GH_TOKEN=<token> scripts/smoke.sh goteborg-norr 64_59
```

## Close

Set `PIXELBYGGET_STANGD` to `"1"` (Settings → Variables, or `wrangler.jsonc` and deploy). Every request but GET gets 503; the grid stays readable.

## Endpoints

All under `/v1`. Errors are `{ "error": "<code>", "message": "..." }`.

| Method | Path | Who | Answers |
| --- | --- | --- | --- |
| GET | `/health` | anyone | 200 |
| GET | `/sheets` | anyone | sheets with `done` and `total` |
| GET | `/cities` | anyone | sheets grouped by city |
| GET | `/grid/{sheet}` | anyone | sheet and cells `[col, row, state, login, updated]`; 404 `no_sheet`. A live cell the admin took down is shown as `last` until its tiles are blanked: it cannot be claimed |
| GET | `/me` | user | login, counts, locks |
| POST | `/claim` `{sheet, col, row}` | user | 201 `expires_at`; 409 `taken` `being_removed`, 403 `sheet_closed` `too_many_locks` `too_many_waiting` (five uploads of a builder who is not trusted already wait for review), 422 `outside`. A lock past its expiry can be taken |
| POST | `/release` `{sheet, col, row}` | user | 200; 403 `not_yours`, 409 `already_uploaded` |
| PUT | `/upload/{sheet}/{col}_{row}` | user | body PNG, header `X-Manifest`; 201 `{state}`; 404 `no_cell`, 403 `not_yours`, 411 `no_length`, 413 `too_big`, 400 `bad_manifest` `short_body`, 422 `not_checked` `wrong_pipeline` `bad_size` `sha_mismatch`, 415 `not_png` `not_plain` (the PNG is not 8-bit RGB, is interlaced, or holds alpha, animation, a gamma, a colour profile or any part but the picture, text, a time, the print size and the sRGB mark), 429 `daily_limit`, 409 `lock_expired` `lock_lost` (the lock ended during the upload; nothing kept). The manifest must have `check: "ok"`; strings are at most 200 characters and unknown keys are dropped |
| GET | `/inkorg/{sheet}/{col}_{row}.png` · `.json` | admin | the cell's current upload; header `X-Inbox-Key` |
| POST | `/approve` `{sheet, col, row}` | admin | 200; 409 `not_waiting` |
| POST | `/reject` `{sheet, col, row, comment}` | admin | 200; 409 `nothing_to_reject` `changed_meanwhile`. The cell goes back to free; its upload is purged after 30 days (a live cell: kept until `/blanked`) |
| POST | `/trust` `{login or github_id, trusted}` · `/block` `{login or github_id, blocked}` | admin | 200; 404 `no_user`, 409 `ambiguous_login` (two rows share it: send `github_id`). Login matches regardless of case. A block frees the cell that builder holds; what they uploaded stays in the queue |
| GET | `/queue?state=vantar\|godkand\|removed` | admin | cells in that state with their eight neighbours; `removed` = live cells taken down, for the intake to blank |
| POST | `/live` `{sheet, cells, rev}` | admin (the intake) | 200 `{live}`; all or nothing: 409 `not_approved` (a listed cell was not approved; nothing changed), 409 `stale_rev` (rev must be greater than the sheet's), 400 `duplicate_cells`, 400 `bad_body` (no cells, or `rev` not a whole number from 1) |
| POST | `/blanked` `{sheet, cells}` | admin (the intake) | 200 `{blanked}`: removed cells whose tiles are blank again; drops their inbox objects |
| POST | `/sheets` (sheet fields + `mask`) | admin | 201 (`resumed: true` when a half-written sheet was completed); 409 `exists`, 400 `bad_body` |
| POST | `/seed` `{sheet, cells, login, github_id}` | admin | 200; 422 `seed_outside_mask` |

Everything answers 503 `closed` when the service is closed (GET excepted), 429 `rate_limited` over 60 requests a minute per client, and 500 `internal` when storage fails. The admin's token, once GitHub has vouched for it, passes the rate limit on admin routes.

States of a cell: `ute` (not offered: outside the built-up area, or no source to draw it from yet), `fri`, `last` (locked), `vantar` (uploaded, waiting for review), `godkand`, `live`. A live cell credited to `opensverige` is one the map showed before Pixelbygget.
