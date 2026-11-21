# Pixelbygget CLI (`rutor`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python command `rutor` that a volunteer (or their agent) runs to log in with GitHub, claim a cell, render and redraw it, and upload the result to the rut-tjänst, non-interactively, with stable output and exit codes.

**Architecture:** One package `cli/rutor/` with a thin argparse front (`__main__.py`), a small HTTP client for the service (`api.py`, urllib only), the GitHub device flow (`login.py`), the manifest and PNG helpers (`manifest.py`), the pinned pipeline constants and hash checks (`pipeline.py`), and one module per command. The redraw core is moved verbatim from `scripts/redraw_tile.py` into `cli/rutor/redraw_core.py` and called on a 1536 px window. The renderer is a module boundary (`cli/rutor/render.py`) whose implementation is ported from the maintainer's PC later; until then it fails with a clear message and exit code 3.

**Tech Stack:** Python 3.11+, standard library for everything but the redraw (torch, diffusers, PIL as in `requirements.txt`, imported lazily). Tests with `unittest` (the repo's existing runner), a fake service on `http.server` in a thread.

**Spec:** `docs/superpowers/specs/2026-11-17-pixelbygget-design.md`, sections 8, 9, 9b. Service contract: `service/README.md` (endpoint table) and `service/src/*.js`.

## Global Constraints

- No new runtime dependencies. Stdlib for the client and the commands; the redraw keeps `requirements.txt` as it is.
- Explicit type hints on every function; no `Any`; no TODO comments; no emojis; named functions, no classes except where argparse or unittest want them.
- Code, identifiers, commit messages in English. User-facing output lines stable and greppable: `key: value` per line (`claimed: goteborg-norr 64_59 expires 2026-11-19T12:00:00Z`).
- Exit codes: 0 ok, 1 bad input, 2 refused by the service (locked, limit, closed, not yours), 3 environment (no CUDA, no key, renderer missing, not logged in), 4 network.
- The GitHub token lives in `~/.config/pixelbygget/token` (override `PIXELBYGGET_HOME`), file mode 0600. The Google key lives in `.env` in the clone as `GOOGLE_MAPS_KEY`; the CLI refuses a `.env` that is group- or world-readable on POSIX and says why.
- Service base url default `https://pixelbygget.gustaf-garnow.workers.dev`, override `PIXELBYGGET_URL`. GitHub OAuth client id from `PIXELBYGGET_GITHUB_CLIENT_ID`, default the placeholder `REPLACE_WITH_CLIENT_ID` (login refuses to start with the placeholder and says so).
- `PIPELINE = "2026.11-a"` in `pipeline.py`, with the model id `addlabsviral/Qwen-Image-2.1-Turbo-4bit`, LoRA id `handful/mndf-pixel-qwen-image-2.1`, window 1024, step 512 copied from `scripts/redraw_tile.py`. The pinned revision hashes are empty strings until the maintainer fills them; an empty pin is reported as `pin: unset` and does not block.
- Work files under `work/<sheet>/<col>_<row>.src.png` (render) and `work/<sheet>/<col>_<row>.png` (redraw); `work/` is gitignored. Only the redraw PNG is ever uploaded.
- The old contributor flow (`claims/`, `results/`, `scripts/claim_district.py`, `scripts/publish_png.py`, `scripts/check_push.py`, `.github/workflows/door.yml`, `tests/test_door.py`) is retired in the last task, not before.
- The file `AGENTS.md` at the repo root describes the OLD flow and must be rewritten in the last task; its rules do not bind this work.

## Review Focus

- A service answer that is not JSON (Cloudflare error page, 502) must print `error: service answered 502, not JSON` and exit 4, never a traceback (Task 2 test `test_non_json_answer_is_exit_4`).
- A `.env` that is world-readable must stop `rutor render` before any network call (Task 3 test `test_world_readable_env_refused`).
- `rutor upload` on a file whose IHDR says 1024 but whose manifest width differs must fail locally with exit 1 before uploading (Task 6 test `test_manifest_mismatch_fails_locally`).
- Device flow: `authorization_pending` and `slow_down` keep polling with the interval GitHub gives, `expired_token` exits 3 with a message to run login again (Task 4 tests `test_poll_slow_down_backs_off`, `test_expired_device_code_exit_3`).
- `rutor claim --next` on a sheet with no free cell adjacent to a live one falls back to any free cell, and on a sheet with no free cell at all exits 2 with `refused: no_free_cell` (Task 5 test `test_next_falls_back_then_refuses`).

---

## File structure

- `cli/pyproject.toml`: package `pixelbygget`, version 0.1.0, `[project.scripts] rutor = "rutor.__main__:main"`, requires-python >= 3.11, no dependencies.
- `cli/rutor/__init__.py`: version string.
- `cli/rutor/__main__.py`: `main(argv: list[str] | None = None) -> int`, argparse with one subparser per command, maps `RutorError` to exit codes, prints `error: <message>` on stderr.
- `cli/rutor/errors.py`: `class RutorError(Exception)` with `code: int` (1–4) and `message: str`; helpers `bad_input(msg)`, `refused(msg)`, `environment(msg)`, `network(msg)`.
- `cli/rutor/config.py`: `home() -> Path`, `token_path() -> Path`, `read_token() -> str | None`, `write_token(token: str) -> None` (0600), `service_url() -> str`, `github_client_id() -> str`, `read_env_key(repo_root: Path) -> str` (reads `.env`, permission check), `repo_root() -> Path`, `work_dir(sheet: str) -> Path`.
- `cli/rutor/api.py`: `request(method: str, path: str, *, token: str | None, body: bytes | None = None, json_body: object | None = None, headers: dict[str, str] | None = None) -> tuple[int, object]` (status, parsed JSON); `get(path, token=None)`, `post(path, json_body, token)`, `put_bytes(path, data, headers, token)`; raises `network` on URLError/non-JSON, `refused` on 4xx/5xx with the service's `error` code in the message as `refused: <code>: <message>`.
- `cli/rutor/login.py`: `start_device_flow(client_id: str) -> dict` (`device_code`, `user_code`, `verification_uri`, `interval`, `expires_in`), `poll_for_token(client_id: str, device_code: str, interval: int, sleep: Callable[[float], None]) -> str`, `cmd_login(args) -> int` (prints the code and url, waits, writes the token, prints `logged in: <login>` after `GET /v1/me`), `--accept-terms` flag and the terms text constant `TERMS` (the four points from the spec, section 8).
- `cli/rutor/manifest.py`: `sha256_file(path: Path) -> str`, `png_size(path: Path) -> tuple[int, int]` (signature + IHDR, raises `bad_input` otherwise), `build_manifest(*, pipeline: str, seed: int | None, png: Path, source: dict[str, str], gpu: str, rendered_at: str) -> dict`, `encode_manifest(manifest: dict) -> str` (base64 JSON), `write_manifest(manifest: dict, path: Path) -> None`.
- `cli/rutor/pipeline.py`: constants above, `PINS: dict[str, str]` (`model_revision`, `lora_sha256`, `diffusers_commit`, each `""`), `prompt_for(city_name: str) -> str` (verbatim from `scripts/redraw_tile.py`), `check_pins(found: dict[str, str]) -> list[str]` (returns the names that are pinned and differ).
- `cli/rutor/redraw_core.py`: the functions of `scripts/redraw_tile.py` moved verbatim (`pad_size`, `get_views`, `window_token_indices`, `edge_pad`, `preflight_message`, `_load_pipe`, `_prepare_window`, `_denoise`, `_decode`) plus `redraw_image(image, prompt: str, seed: int | None) -> Image` that pads, encodes, denoises, decodes and crops as `redraw()` did, without any district, claim or file logic.
- `cli/rutor/render.py`: `render_cell(sheet: dict, col: int, row: int, key: str, out: Path) -> None`; v1 body raises `environment("renderer not ported yet: copy the Cesium renderer into cli/rutor/renderer/ (see docs/superpowers/specs, section 9)")` unless `cli/rutor/renderer/__init__.py` exists and exports `render(sheet, col, row, key, out)`, in which case it calls it. `camera_for(sheet: dict, col: int, row: int) -> dict` computes the cell's ground box from the sheet (origin, x_meters, y_meters, 0.25 m per px, 1536 px window centred on the 1024 cell) and is unit-tested.
- `cli/rutor/commands.py`: `cmd_cities`, `cmd_grid`, `cmd_claim`, `cmd_release`, `cmd_status`, `cmd_whoami`, `cmd_preflight`, `cmd_render`, `cmd_redraw`, `cmd_upload`, `cmd_approve`, `cmd_reject`, `cmd_queue`, `cmd_trust`, each `(args: argparse.Namespace) -> int`.
- `cli/tests/fake_service.py`: `FakeService` on `http.server` in a thread with an in-memory dict of sheets and cells, honouring the routes the CLI uses (`/v1/me`, `/v1/sheets`, `/v1/grid/{sheet}`, `/v1/claim`, `/v1/release`, `PUT /v1/upload/...`, `/v1/queue`, `/v1/approve`, `/v1/reject`, `/v1/trust`), returning the service's JSON shapes and error codes, plus a `fail_next_with(status, body)` switch.
- `cli/tests/test_*.py`: one per module.
- `cli/README.md`: install and the five steps for a human; `AGENTS.md` at the repo root rewritten for the new flow.

Run tests from `cli/`: `python3 -m unittest discover -s tests -v`.

---

### Task 1: Package skeleton, errors, config

**Files:**
- Create: `cli/pyproject.toml`, `cli/rutor/__init__.py`, `cli/rutor/__main__.py`, `cli/rutor/errors.py`, `cli/rutor/config.py`, `cli/tests/__init__.py`, `cli/tests/test_config.py`, `cli/tests/test_main.py`
- Modify: `.gitignore` (add `work/`, `cli/**/__pycache__/`)

**Interfaces:** Produces everything listed for `errors.py`, `config.py` and `main()` above.

- [ ] **Step 1: Write failing tests** in `test_config.py`: `test_token_roundtrip_is_0600` (write then read under a tmp `PIXELBYGGET_HOME`; `stat` mode & 0o777 == 0o600), `test_service_url_default_and_override`, `test_read_env_key_reads_GOOGLE_MAPS_KEY` (tmp `.env` with mode 0600), `test_world_readable_env_refused` (mode 0644 → `RutorError` code 3, message mentions `chmod 600 .env`), `test_missing_env_is_environment_error`. In `test_main.py`: `test_no_command_prints_usage_exit_1`, `test_rutor_error_maps_to_exit_code` (a stub command registered in a test raising `refused('x')` → `main` returns 2 and stderr has `error: x`), `test_version_flag`.
- [ ] **Step 2: Run** `cd cli && python3 -m unittest discover -s tests -v` → FAIL (modules missing).
- [ ] **Step 3: Implement** the three modules and `pyproject.toml`. `main()` builds the parser with a `register(sub)` function per command module; command modules are imported lazily inside `main` so a broken torch install never breaks `rutor cities`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(cli): package skeleton, errors and config`.

### Task 2: Service client

**Files:**
- Create: `cli/rutor/api.py`, `cli/tests/fake_service.py`, `cli/tests/test_api.py`

**Interfaces:** Produces `request`, `get`, `post`, `put_bytes` as in File structure. `FakeService` with `start() -> str` (base url), `stop()`, `state` (dict with `sheets`, `cells` keyed `"sheet/col_row"`, `users`), `fail_next_with(status: int, body: bytes, content_type: str)`.

- [ ] **Step 1: Write failing tests**: `test_get_parses_json`, `test_bearer_header_sent` (fake records headers), `test_refused_carries_service_code` (fake answers 409 `{error:'taken'}` → `RutorError(code=2)` message `refused: taken: ...`), `test_non_json_answer_is_exit_4` (fake answers 502 text/html → code 4, message `service answered 502, not JSON`), `test_connection_refused_is_exit_4` (closed port), `test_put_bytes_sends_body_and_headers`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** with `urllib.request`, 30 s timeout, `User-Agent: rutor/<version>`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(cli): service client and fake service for tests`.

### Task 3: Manifest, pipeline pins, redraw core move

**Files:**
- Create: `cli/rutor/manifest.py`, `cli/rutor/pipeline.py`, `cli/rutor/redraw_core.py`, `cli/tests/test_manifest.py`, `cli/tests/test_pipeline.py`, `cli/tests/png.py` (`minimal_png(width, height) -> bytes`, signature + IHDR + IEND)
- Modify: `scripts/redraw_tile.py` → thin wrapper importing `redraw_core` (keeps the old CLI working until Task 8 retires it)

**Interfaces:** as in File structure.

- [ ] **Step 1: Write failing tests**: `test_png_size_reads_ihdr`, `test_png_size_rejects_not_png`, `test_sha256_file`, `test_build_manifest_has_all_keys` (keys exactly `pipeline, seed, sha256, width, height, source, gpu, renderedAt`), `test_encode_manifest_is_base64_json`, `test_prompt_for_matches_old_script` (string equality with the literal in the old script), `test_check_pins_ignores_empty_pins`, `test_check_pins_reports_mismatch`, and `test_redraw_core_pure_functions` (`pad_size(1536) == 1536`, `get_views(1536, 1536)` has 4 windows, `window_token_indices` length for a 1024 window at scale 16 is 4096).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; move the functions, do not change their bodies. **Step 4: Run** → PASS; also `python3 -m unittest tests/test_door.py` from the repo root still passes. **Step 5: Commit** `refactor(cli): manifest, pipeline pins and redraw core module`.

### Task 4: Login (device flow)

**Files:**
- Create: `cli/rutor/login.py`, `cli/tests/test_login.py`

**Interfaces:** as in File structure. GitHub endpoints: `POST https://github.com/login/device/code` (`client_id`, `scope` empty) and `POST https://github.com/login/oauth/access_token` (`client_id`, `device_code`, `grant_type=urn:ietf:params:oauth:grant-type:device_code`), both with `Accept: application/json`. The HTTP calls go through an injectable `http: Callable[[str, dict[str, str]], dict]` so tests pass a stub.

- [ ] **Step 1: Write failing tests**: `test_start_returns_codes`, `test_poll_returns_token_when_granted`, `test_poll_slow_down_backs_off` (stub answers `slow_down` then token; recorded sleeps are `interval+5` then done), `test_poll_authorization_pending_keeps_waiting`, `test_expired_device_code_exit_3`, `test_placeholder_client_id_refused`, `test_login_requires_accept_terms_or_prints_terms` (without the flag and with stdin not a tty → exit 1 and `TERMS` printed).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; `cmd_login` prints `code: XXXX-XXXX`, `url: https://github.com/login/device`, then `logged in: <login>` after `/v1/me`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(cli): github device-flow login`.

### Task 5: Read and claim commands

**Files:**
- Create: `cli/rutor/commands.py` (`cmd_cities`, `cmd_grid`, `cmd_claim`, `cmd_release`, `cmd_status`, `cmd_whoami`), `cli/tests/test_commands.py`
- Modify: `cli/rutor/__main__.py` (register them)

**Interfaces:** `rutor cities [--json]` prints `sheet: <id> <state> <done>/<total>` per line. `rutor grid <sheet> [--free] [--json]` prints `cell: <col>_<row> <state> [<login>]`. `rutor claim <sheet> <col>_<row>` and `rutor claim <sheet> --next`: `--next` picks a free cell with at least one live 8-neighbour, nearest the sheet centre, else any free cell, else `refused: no_free_cell` exit 2. `rutor release <sheet> <col>_<row>`. `rutor status` = `/v1/me` locks as `lock: <sheet> <col>_<row> expires <ts>`. `rutor whoami` prints `login: <login>`.

- [ ] **Step 1: Write failing tests** against `FakeService`: `test_cities_lines`, `test_grid_free_filter`, `test_claim_prints_expires`, `test_claim_taken_is_exit_2`, `test_next_prefers_cell_next_to_live`, `test_next_falls_back_then_refuses`, `test_release_ok`, `test_status_lists_locks`, `test_commands_need_login` (no token → exit 3 `not logged in: run rutor login`).
- [ ] **Step 2–5:** RED, implement, GREEN, commit `feat(cli): cities, grid, claim, release, status, whoami`.

### Task 6: Preflight, render, redraw, upload

**Files:**
- Create: `cli/rutor/render.py`, `cli/tests/test_render.py`, `cli/tests/test_upload.py`
- Modify: `cli/rutor/commands.py` (`cmd_preflight`, `cmd_render`, `cmd_redraw`, `cmd_upload`)

**Interfaces:** `camera_for(sheet, col, row)` returns `{ "center_lat", "center_lon", "half_width_m": 192.0, "half_height_m": 192.0, "heading": sheet["heading"], "ground_height" }` where the cell centre is at pixel `(col*1024+512, row*1024+512)` of the sheet image, converted with `x = x_meters + px*0.25`, `y = y_meters - py*0.25`, `lat = origin_lat + y/111320`, `lon = origin_lon + x/(111320*cos(origin_lat))`. `rutor preflight` prints one `check: <name> ok|missing <why>` per line (python version, torch cuda, bitsandbytes, chrome, env key, service health, login) and exits 3 if any is missing. `rutor render <sheet> <col>_<row>` writes `work/<sheet>/<col>_<row>.src.png` (1536 px). `rutor redraw <sheet> <col>_<row> [--seed N] [--wait-for-cheap]` reads the `.src.png`, pads nothing (already 1536), runs `redraw_image` with `prompt_for(sheet.name)`, writes `work/<sheet>/<col>_<row>.png` and `.manifest.json` (pipeline, seed, sha256 of the result, width, height, source from the renderer's info file `work/<sheet>/<col>_<row>.src.json` if present else `{provider: "unknown", version: "unknown"}`, gpu from torch, renderedAt). `rutor upload <sheet> <col>_<row>` verifies the PNG size matches the manifest, PUTs with `X-Manifest`, prints `uploaded: <sheet> <col>_<row> state <state>` and `url: ...`.

- [ ] **Step 1: Write failing tests**: `test_camera_for_center_of_cell` (Göteborg sheet from the spec, cell 64_59 → lat 57.691 ± 0.001, lon 11.967 ± 0.001), `test_render_without_renderer_is_exit_3_with_port_message`, `test_world_readable_env_refused` (render), `test_upload_sends_manifest_header_and_prints_state`, `test_manifest_mismatch_fails_locally` (no request reaches the fake), `test_upload_refused_passes_code` (fake answers 422 `wrong_pipeline` → exit 2), `test_preflight_lines` (with torch absent → `check: torch missing`). The redraw command's torch path is not unit-tested; `cmd_redraw` is tested with `redraw_image` monkeypatched to return a 1536 px image.
- [ ] **Step 2–5:** RED, implement, GREEN, commit `feat(cli): preflight, render boundary, redraw and upload`.

### Task 7: Admin commands

**Files:**
- Modify: `cli/rutor/commands.py` (`cmd_queue`, `cmd_approve`, `cmd_reject`, `cmd_trust`), `cli/tests/test_commands.py`

**Interfaces:** `rutor queue [--state vantar|godkand] [--show DIR]` prints `waiting: <sheet> <col>_<row> <login> uploaded <ts>` and with `--show` downloads `/v1/inkorg/...png` for each into DIR. `rutor approve <sheet> <col>_<row>`, `rutor reject <sheet> <col>_<row> --comment TEXT`, `rutor trust <login> [--off]`.

- [ ] **Step 1: Write failing tests**: `test_queue_lines`, `test_queue_show_downloads_png`, `test_approve_ok`, `test_reject_requires_comment`, `test_trust_and_off`, `test_admin_refused_is_exit_2`.
- [ ] **Step 2–5:** RED, implement, GREEN, commit `feat(cli): admin queue, approve, reject, trust`.

### Task 8: Retire the git door, docs

**Files:**
- Delete: `claims/`, `results/`, `inputs/`, `scripts/claim_district.py`, `scripts/publish_png.py`, `scripts/check_push.py`, `scripts/repo.py`, `scripts/redraw_tile.py`, `tests/test_door.py`, `.github/workflows/door.yml`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/access-request.yml`, `CLAIM.md`, `districts.json`
- Move: `scripts/se3_price.py` → `cli/rutor/se3_price.py` (used by `--wait-for-cheap`)
- Rewrite: `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md` (keep `@AGENTS.md`), `requirements.txt` unchanged
- Create: `cli/README.md`

- [ ] **Step 1:** `README.md`: what Pixelbygget is in two paragraphs, the five steps (`rutor login`, `rutor claim goteborg-norr --next`, `rutor render`, `rutor redraw`, `rutor upload`), install per OS (keep the existing CUDA/PyTorch install text), the Google key paragraph (own account, own key, own agreement; the key stays in `.env`; nothing Google-derived is uploaded; wording to be confirmed by the maintainer), licence and credit (MIT for the PNG, shown on karta.opensverige.se, credited by GitHub login). `AGENTS.md`: the order of commands, the stop conditions (preflight not ok, no key, service refuses twice, redraw fails), the two human steps (approve login in the browser, put the key in `.env`), never upload `.src.png`, never edit `pipeline.py`. `CONTRIBUTING.md`: rules in the spec's words (one cell at a time until trusted, own key, review of first ten, how to report).
- [ ] **Step 2:** Delete the listed files; run `cd cli && python3 -m unittest discover -s tests -v` → PASS; `grep -rn "claim_district\|check_push\|districts.json" --include=*.md --include=*.py .` → no hits outside `docs/`.
- [ ] **Step 3: Commit** `chore: retire the git door, document the rutor flow`.
