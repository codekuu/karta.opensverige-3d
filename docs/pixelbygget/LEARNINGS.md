# Pixelbygget learnings

What we tried, what we measured and what we changed. Newest first. One entry per finding, with the number that decided it. Images, renders and raw logs stay outside the repository; this file keeps the conclusions.

## 2026-11-21

### Review before opening
Four reviews read the code without running the pipeline. What they found and what changed:
- **The overlap was not exact.** Tree crown tables were float32 and held each cell's own photo x, so 7 pixels differed between east-west neighbours. The test allowed 0.1 percent (786 pixels). Fix: float64, and the test now demands zero.
- **Uploads shared one inbox key per cell.** A retry, or someone taking the cell during a slow upload, could make the loser delete the winner's image. Fix: one key per upload (cell plus sha256 prefix); a request deletes only its own key, and only when the cell does not point at it.
- **A removed live cell could be claimed again** before the intake blanked it, and the blanking then deleted the new owner's upload. Fix: no claims while removed.
- **Uploads after the lock's expiry were accepted** until the hourly cron ran. Fix: the service checks the expiry, and expired locks no longer count against the limit.
- **Forests mapped as multipolygon relations were never drawn**, and a way Overpass sent twice could lose its tags.
- **Network timeouts crashed with a traceback** instead of exit 4, and an Overpass error answer with status 200 was cached forever.
- Docs told contributors to run preflight before login, which can never pass, and the login terms still mentioned a Google key.

### Opening the repository
- The full history was checked before going public: no images, no keys, no env files, 29 KB packed. The Google-era plate pipeline only exists on the branch `cursor/plate-pipeline-7eb5`, as code.
- A service test seeded uploads at a fixed date while the route counts the last 24 hours from the real clock, so it failed from 2026-11-18. Tests that meet the real clock must seed relative to it.
- Pull requests now run both test suites in GitHub Actions (`.github/workflows/tests.yml`). The CLI tests need only Pillow and numpy, no GPU and no model.

## 2026-11-20

### First end-to-end contributor run
- The whole volunteer flow ran on a Windows gaming PC (RTX 5070, 12 GB) against the service running locally with `wrangler dev`: preflight, claim `--next`, render from open data, redraw, quality check, upload, then the admin's queue, inbox download (same sha256) and approve. Every step worked without code changes.
- Login without a GitHub app: the service caches who a token belongs to in KV under `gh:<sha256 of token>`, so a local test seeds two fake users with `wrangler kv key put --binding GH --local` and uses made-up tokens. GitHub is never asked and no real token is used. The admin id comes from `service/.dev.vars` (`ADMIN_GITHUB_ID`), which is now ignored by git.
- Reaching a service on one machine from another: when a firewall blocks the port, a reverse SSH tunnel (`ssh -N -R 8787:localhost:8787 <gpu-host>`) works.
- RTX 5070 with 12 GB is enough: one full `rutor redraw` took 133 s including model load and the check.
- The same cell and seed is not pixel-identical across GPUs and library versions (RTX 5070 with diffusers 0.42.1 against an L4 with the pinned commit: mean difference 23). The look is the same; blending covers the seams, but a cell cannot be reproduced exactly on another machine.
- `rutor queue --state godkand` printed approved cells as "waiting"; fixed.
- On Windows, run PowerShell through script files (`powershell -File`); nested quoting over SSH breaks. Tar archives from macOS carry `._*` files unless `COPYFILE_DISABLE=1` is set.

## 2026-11-19

### Lidingö against the Google map (16 cells, west Lidingö)
- The chain ran clean: 130 s per cell on an L4, all 16 cells passed the check, no visible seams after blending, and the render is identical on the Mac and on the box.
- It looks clearly worse than the Google-based map in three ways, each with a known cause:
  - **Villas drawn as five-storey blocks.** 89 percent of Lidingö's OSM buildings have no height or levels (Göteborg: 65 percent), and the renderer falls back to 18 m. A height from footprint size and building type, or laser heights from Lantmäteriet, fixes it.
  - **Forest drawn as lawn.** The 2019 photo is from April with bare trees, and OSM has few forest polygons on the slopes. Land cover data (Nationella Marktäckedata, open) or laser crown heights would place the trees.
  - **Ghost houses in the water.** Lidingö's photo stops at the municipal border, so cells reaching the Stockholm side draw OSM buildings on an empty photo. A cell needs full photo coverage, or several sources stitched together.
- Granite cliffs and rocky shores are missing: no data source in the chain describes them yet.

### Open orthophotos around Stockholm
- **Lidingö** publishes its 2019 orthophoto as CC0: WMS `https://karta.lidingo.se/wms?servicename=wms_ortofoto_2019_oppendata`, layer `theme-ortofoto2019`, `EPSG:4326` (latitude first in WMS 1.3.0). Sharper than Göteborg's.
- **Stockholms stad** orthophoto WMS (`WMS_STHLM_ORTOFOTO_2020`) says all rights reserved and no use without written consent. Do not use it.
- **Solna, Sundbyberg, Danderyd** have no open orthophoto on the EU data portal. North Stockholm needs Lantmäteriet (Geotorget account).
- The renderer now takes a CRS per source and endpoints that already carry a query string.

### The live Google cities do not cover everything
- The Stockholm sheet's rendered "island" leaves out most of Lidingö, including Lidingö centrum. Check the sheet's level-10 overview before choosing cells to compare.

### Lift on the Stockholm sheet
- Scanning lift heights against the live map on two Lidingö cells scored best at 35 and 40 m. Göteborg measured 34.7 m. One constant (34.7 m) serves both sheets.

### Redraw on an L4 (24 GB)
- **Out of memory, cause 1:** the window encode ran with autograd on. One 1024 px window held over 21 GB. Fix: `torch.inference_mode` around the whole redraw.
- **Out of memory, cause 2:** all parts stayed on the GPU. Fix: per phase only the parts that phase uses (encode: text encoder and VAE, denoise: transformer, decode: VAE). Peak is now 10.7 GB.
- **Measured time:** 100 cells, mean 133.3 s, standard deviation 1.4 s, with the model loaded once. The first cell takes about 144 s.
- **Cost:** about 2.60 USD for 100 cells on Vast.ai (0.47 USD per hour).

### Seams after the redraw
- Each cell is redrawn alone, so cells cut at the cell edge show a step in tone and colour (seam step about 2x an ordinary pixel step).
- Cross-fading neighbours over their shared 512 px (128 m) removes it (seam step 0.7 to 1.2, the same as inside a cell). This belongs in the step that assembles the map, not with the person who redraws.

### Quality check calibration
- Over 100 L4 redraws, correct pairs score a match peak of 0.088 and up; a redraw of the wrong cell scores 0.011 to 0.018. The limit is 0.04.
- Open water has almost no edges to align against. A source with under 3 percent clear edges skips the alignment test; the other checks still apply.

### Running on rented boxes
- A slow home uplink (about 20 kB/s) makes uploads to rented boxes the bottleneck. Render sources on the box from the open WMS, upload only code and OSM extracts (a few MB, gzipped).
- The `pytorch/pytorch:2.8.0` image ships old `brotli` and `zstandard`; Hugging Face downloads then crash in `httpx2` with `process() takes no keyword arguments`. Fix: `pip install -U brotli zstandard`.
- Start long jobs with `setsid nohup ... &` and `ssh -n`, or the job dies with the SSH session. Vast boxes can refuse the direct port for minutes while they install SSH; the `sshN.vast.ai` proxy address may also lag.
- `pgrep -f pattern` over SSH matches the SSH command line itself. Grep the process list for the script name instead.
- On the Mac the shell is zsh, which does not split `$var` into words: `set -- $addr` keeps "host port" as one argument and every SSH attempt fails with "Bad port". A wait loop built that way reported a box as down for 15 minutes while it was up. Pass host and port as separate variables.
- Never type an API key for someone. Vast keys go in through a terminal script that reads them without echo.

## 2026-11-18

### Seams in the source renders
Neighbouring 1536 px windows overlap by 512 px and must draw identical pixels there. Four causes were found and fixed, each pinned by `test_neighbour_cells_agree_in_their_overlap`:
- The WMS resamples every request on its own, so two overlapping requests differed in 13 percent of pixels. Fix: fetch the photo as shared 1024 px tiles on the sheet's 0.25 m grid (PNG) and cut every cell from them.
- With the box snapped to the grid, photo pixel centres land exactly between screen columns and rounding follows floating-point noise. Fix: floor, and one longitude scale per sheet.
- Tree randomness came from one sequential generator, so the same tree looked different in each cell. Fix: randomness keyed to world position.
- Window bays counted from the window's column, and wall shading used the tallest wall in the row (which reaches outside the window). Fix: count from the sheet column, shade each wall by its own height.
- Smaller ones: `int(ppm)` gave 7 in one cell and 8 in the next, float32 running sums lost precision with distance, crown texture used each crop's own mean.

### Data fetching
- Overpass answers 502/504 often. Ask once for a whole block and copy the result into each cell's cache instead of two queries per cell.

### GPU estimate method
- Cells per city: SCB tätort 2023 polygon plus 500 m, rasterised to cells (256 m east-west by 362 m north-south). This matches the existing Malmö sheet (1 355 computed, 1 348 actual).
- The free model (Qwen-Image-Edit-2511) has an Apache 2.0 four-step distillation, `lightx2v/Qwen-Image-Edit-2511-Lightning`.

## 2026-11-17

### Google is closed
- Google Photorealistic 3D Tiles are not served to EEA-billed projects created after 2025-07-08, and the terms forbid derived works. The pixel LoRA (`handful/mndf-pixel-qwen-image-2.1`) is trained on Google renders with Codex-painted targets, and Qwen-Image-2.1 is under a research licence. New cities come from open data; the style model must be replaced before an open release.

### What open data gives
- OpenStreetMap alone gives blocky renders that redraw poorly.
- Göteborg's CC0 orthophoto draped on OSM footprints, plus procedural facades, OSM and photo-detected trees and roof colour grading, redraws close to the live Google-based map.
- `camera_for` was missing the cos 45 north-south factor. A fixed 34.7 m lift aligns our renders with the live sheet.
- Redraw on an L40S: 57 s per cell with the model loaded.
