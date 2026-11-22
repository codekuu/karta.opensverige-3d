# Pixelbygget design

How the parts fit together. The commands are in [README.md](../../README.md), the rules in [CONTRIBUTING.md](../../CONTRIBUTING.md), the service API in [service/README.md](../../service/README.md). What we measured and why things are the way they are is in [LEARNINGS.md](LEARNINGS.md).

## The idea

The pixel cities on karta.opensverige.se are drawn cell by cell. Anyone with an NVIDIA card claims a cell, renders it from the sheet's source, redraws it as pixel art on their own machine and uploads the result. An admin reviews it, and the cell lights up on the map with the builder's GitHub login.

## Sheets and cells

- A city is one or more **sheets**. A sheet has an origin (latitude, longitude), a size in cells and a fixed camera. Today: `goteborg-norr` (130 by 118 cells) and `stockholm-norr` (144 by 70).
- A **cell** is 1024 px at 0.25 m per pixel: 256 m east-west and 362 m north-south (the camera looks down at 45 degrees).
- The render and the redraw work on a **1536 px window**: the cell plus 256 px on each side. Neighbouring windows overlap by 512 px.
- Cells outside the built-up area are `ute` and are never drawn.

## The parts

| Part | Where | What it does |
| --- | --- | --- |
| `rutor` | `cli/` in this repo | Login, claim, render, redraw, check, upload, release. |
| Service | `service/` in this repo, a Cloudflare Worker | Locks, uploads, the review queue, the grid. |
| Intake | private, run by the maintainer | Takes approved cells, blends their seams, cuts map tiles and publishes them. |
| Map | karta.opensverige.se | Shows the live tiles and the build page. |

Contributors never get write access to a repo, a bucket or a key. The only thing that leaves a contributor's machine is the redrawn PNG and its manifest.

## A cell's life

`fri` → `last` (locked, 48 hours) → `vantar` (uploaded, waiting for review) → `godkand` (approved) → `live` (on the map).

- A new contributor holds one lock at a time and uploads at most 20 cells a day. Their first ten cells are reviewed.
- After ten approved cells a contributor is trusted: up to eight locks, and uploads go straight to `godkand`.
- A rejected cell goes back to `fri` with a comment. Its upload is kept for 30 days, then purged.
- A live cell the admin takes down is `removed` until the intake has blanked its tiles. Nobody can claim it before that.

## The render

Each sheet has one source, set in `SHEET_KINDS` in `cli/rutor/renderer/sources.py`, so neighbouring cells never mix sources.

### Open data (`goteborg-norr`)

`rutor render` builds the 1536 px source window from open data only:

- **Orthophoto** from a WMS, fetched as shared 1024 px tiles on the sheet's 0.25 m grid, so every cell cuts its photo from the same pixels. Göteborgs Stad (CC0) today. Lidingö and Lantmäteriet are open issues.
- **OpenStreetMap** from Overpass, pinned to one date per sheet, for buildings, roads, water and trees.
- Buildings are extruded with the camera's fixed lift. Facades, windows and tree crowns come from rules keyed to world position, never to the cell.

The rule that matters: **two neighbouring cells draw identical pixels in their 512 px overlap, on any machine.** `test_neighbour_cells_agree_in_their_overlap` pins it with zero differing pixels. A change to the renderer must keep that test green.

### Google (`stockholm-norr`)

`cli/google-render/render.mjs` opens Cesium in headless Chrome, loads Google Photorealistic 3D Tiles (Cesium ion asset 2275207) with the contributor's own token, and captures one orthographic 1536 px frame.

- The camera is the one that drew the live map: one orthographic camera per sheet, fixed in the earth-centred frame at the sheet origin, looking along the heading and 45 degrees down, moved sideways in its picture plane to each cell. A point lands on the map pixel the site's own projection gives it, terrain included, because Google's tiles carry their heights. `test_the_pose_is_the_one_that_drew_the_live_map` pins it to numbers from that renderer (`grid-config.ts`).
- The token goes to node in its environment and to the page in memory, never on a command line, in a file or in a logged URL.
- Streamed tiles are not pixel-identical between machines, so the overlap test does not apply. The intake blends the seams.
- Google's terms bind the token. `rutor render` prints that on every Google cell, and the manifest names the source, so those cells can be found and taken down if Google asks.
- The sheet is already live, so its mask is the live island: 5 036 cells, the ones the map's edge picture draws. It is redrawn, not extended.

## The redraw

`rutor redraw` runs the pinned diffusion pipeline over the window in four 1024 px tiles, then crops the centre 1024 px. One cell takes about two minutes on a 12 GB card. Each phase puts only its own parts on the GPU, so the peak is about 11 GB.

Redraws are not pixel-identical across GPUs and library versions. That is fine: the intake blends neighbours over their overlap, so no seam shows.

## The quality check

`rutor check` compares the redraw with its source. It catches a blank, too dark, posterised, shifted or unchanged redraw, and a redraw of the wrong cell. It was calibrated on 100 redraws. A cell that fails exits with code 5 and cannot be uploaded. The service also refuses a manifest that does not say the check passed.

## Uploads and the inbox

- An upload is one PUT with the PNG as the body and the manifest in a header.
- The service checks the lock, its expiry, the size, the manifest, every PNG chunk and the sha256 before it writes anything.
- Each upload is stored under its own key (the cell, the start of its sha256 and a random part). A request that loses its lock can only delete its own objects, never someone else's.

## What is open and what is not

Open, in this repo: the CLI, the renderer, the redraw call, the quality check and the service. Not open: the intake (blending, tile cutting, publishing to the live bucket) and the maintainer's machines.
