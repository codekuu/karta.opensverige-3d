# Rut-tjänsten (`pixelbygget`)

The service behind Pixelbygget: one Cloudflare Worker that locks grid cells, takes finished cells into an inbox bucket, serves the grid to the build page and the CLI, and lets the admin review. It is the single source of truth for who holds which cell. The design is in `docs/superpowers/specs/2026-11-17-pixelbygget-design.md` (sections 5, 6, 7, 9b, 13).

State lives in D1 (`sheets`, `cells`, `users`, `events`), uploads in the R2 bucket `pixelbygget-inkorg`, GitHub user lookups in KV for ten minutes. The caller sends the GitHub user token from the device flow as a Bearer header; the service asks GitHub who it is and keeps no sessions and no secrets of its own. The one admin is the GitHub id in `ADMIN_GITHUB_ID`. A cron runs every hour: locks older than 48 hours go back to free, inbox objects of cells sent back more than 30 days ago are deleted.

## Create once (Gustaf)

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

The CLI needs a GitHub OAuth App named "Pixelbygget" under the organisation with "Enable Device Flow" ticked; its client id goes into the CLI, not here.

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

```bash
npm install
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
| GET | `/grid/{sheet}` | anyone | sheet and cells `[col, row, state, login, updated]`; 404 `no_sheet` |
| GET | `/me` | user | login, counts, locks |
| POST | `/claim` `{sheet, col, row}` | user | 201 `expires_at`; 409 `taken`, 403 `sheet_closed` `too_many_locks`, 422 `outside` |
| POST | `/release` `{sheet, col, row}` | user | 200; 403 `not_yours`, 409 `already_uploaded` |
| PUT | `/upload/{sheet}/{col}_{row}` | user | body PNG, header `X-Manifest`; 201 `{state}`; 403 `not_yours`, 413 `too_big`, 400 `bad_manifest` `short_body`, 422 `wrong_pipeline` `bad_size` `sha_mismatch`, 415 `not_png`, 429 `daily_limit` |
| GET | `/inkorg/{sheet}/{col}_{row}.png` | admin | the object |
| POST | `/approve` `{sheet, col, row}` | admin | 200; 409 `not_waiting` |
| POST | `/reject` `{sheet, col, row, comment}` | admin | 200; 409 `nothing_to_reject` |
| POST | `/trust` `{login, trusted}` · `/block` `{login, blocked}` | admin | 200; 404 `no_user` |
| GET | `/queue?state=vantar` | admin | waiting cells with neighbours |
| POST | `/live` `{sheet, cells, rev}` | admin (the intake) | 200 `{live}`; 409 `not_approved` |
| POST | `/sheets` (sheet fields + `mask`) | admin | 201; 409 `exists`, 400 `bad_body` |
| POST | `/seed` `{sheet, cells, login, github_id}` | admin | 200; 422 `seed_outside_mask` |

Everything answers 503 `closed` when the service is closed (GET excepted) and 429 `rate_limited` over 60 requests a minute per client.

States of a cell: `ute` (outside the city), `fri`, `last` (locked), `vantar` (uploaded, waiting for review), `godkand`, `live`.
