# Pixelbygget learnings

What we tried, what we measured and what we changed. Newest first. One entry per finding, with the number that decided it. Images, renders and raw logs stay outside the repository; this file keeps the conclusions.

## 2026-11-23

### A new city without a terrain model
- After PR 17 an open sheet needed a city terrain model, so a new city like Eslöv stopped with "no open terrain model". A sheet without one is now drawn on flat ground at its `ground_height`, and its manifest says `terrain flat`. A new city has no older map to line up with, so flat ground only costs hills their height.

### The open renderer is placed by the live map's camera, on real ground
- `rutor/frame.py` holds the live frame for both sources. Its points match `grid-config.ts` to 1e-6 m at the origin, at the sheet's corners and on a 110 m hill.
- Before: `camera_for` mapped degrees to metres with one cos(origin_lat), and `draw.py` put all ground 34.7 m above a flat plane. Over Göteborg's 3 071 masked cells that missed the live map at sea level by a median 40 px and at most 174 px, mostly sideways. Higher ground missed by 2.8 px more per metre. 20 km from the origin, curvature alone drops the ground 31 m.
- Ground heights come from Göteborgs Stad Höjdmodell 2022 (CC0, 0.5 m, RH 2000). It covers all 3 071 masked cells. Lantmäteriet's national model answers 401 without a login. The city ships it as 10 km zips of up to 700 MB, but a zip has its directory at the end: six HTTP range requests in 1.2 s fetch one 1 km tile, about 7 MB. Pillow reads the LZW float32 GeoTIFF, so nothing new is installed.
- RH 2000 to the ellipsoid: SWEN17_RH2000 is 36.49 m and 36.92 m at Lantmäteriet's two nearest control points. A plane through three points gives 36.7 m at the origin, within about 0.5 m (1.5 px) over the sheet. The old renderer's comment already said "sea level in Gothenburg about 36 m".
- The SWEREF 99 TM formula is checked against Lantmäteriet's STAC box for tile 640_32 to 1e-7 degrees.
- Compared with the live tiles by correlating blurred brightness in boxes around strong ground features. Edge correlation over a whole cell is useless against pixel art (score 0.05).
  - Cell 64_52, the canals by the origin, rendered from real data in 168 s: 0 to 8 px off in four boxes (correlation 0.58 to 0.73).
  - Cell 63_19, a forested hill, rendered in 78 s: 14 to 46 px off in three boxes (0.56 to 0.68), 0 px over the whole cell (0.40). The old renderer was 34 to 120 px off in the same boxes. Google's mesh over a forest is the canopy, not the ground, which may explain part of the rest.
- The new boxes sit where the cells really are, so the mask's photo cover rule moves with them. Rerun with the sheet's ground height, 9 of the 3 071 masked cells no longer have full photo cover: 36_55, 40_55, 58_95, 62_62, 72_59, 74_59, 77_60, 78_60 and 84_42.
- Keeping neighbours identical on slopes: positions are floored in map pixels and moved by whole pixels. Trigonometry runs in Python per row and per column, so per-pixel work is only `+`, `-` and `*`. Ground is read on a 2 m lattice counted from the origin. The canvas reaches 32 px past the window, so gaps on slopes steeper than 22 degrees are filled from the same neighbours in both cells. The overlap test now runs on a ridge that is 58 degrees at its steepest and still finds 0 differing pixels.
- Real neighbours 63_19 and 64_19, each centred on its own ground with its own photo box and terrain tiles, drew identical pixels in all 786 432 of their overlap.
- The source manifest's version now ends in `terrain hojdmodell_2022`. The service does not check it, so a render from an older checkout can only be caught in review.
- A photo row lands 0.7 px below the row north of it and paints over it, so the southern row of a mark can hide under the next row. A test of where a pixel lands must look at a row that something nearer cannot cover.

### Single trees at a pinned date break Overpass
- `node["natural"="tree"]` with `[date:...]` runs out of memory (2 GB, `node_tags_global.bin`) on overpass-api.de and maps.mail.ru, even for one cell and even with the box first. kumi.systems and private.coffee timed out. Tree areas and relations at the same date answer in about 33 s.
- Single trees now come from their own query without the date: today's trees whose current version is older than the pin (`(if: timestamp() < "...")`). For 63_19 that is all 159 of today's trees. A tree someone edits or deletes later drops out of later renders, and neighbours rendered on either side of that edit can differ by that tree. The intake blends seams.
- A friend's agent hit the same failure on an Eslöv cell and fixed it in their own way. That fix is not in this repo.

## 2026-11-22

### The Google camera is the live map's camera
- The live tiles were rendered by `renderer/grid-config.ts` on the PC: one orthographic camera per sheet, fixed at the origin's tangent plane, moved sideways per shot. The site's `geometri.ts` projects with the same frame. Copying that pose into `google.py` matches the old code to 5e-10 m.
- The first Google camera reused the open renderer's shortcut, a target at `ground_height - lift_m` with local axes. Against the live pose it missed by 16 to 65 px vertically, and by up to 352 px across at the sheet's corners.
- The open renderer has the same two gaps against the live map. `camera_for` maps degrees to metres with one cos(origin_lat): Göteborg's masked cells sit a median 41 px and at most 176 px (44 m) from the live frame, mostly across. And it draws all ground at one height: cell 63_19 lies 110 m above the sea, about 300 px higher on the live map, and an edge match put it about 230 px off.
- The Overpass tree query for 63_19 failed five times: out of memory once, then HTTP 502.

### Stockholm's mask is the live island
- Stockholm norr is already on the map, so it is redrawn cell for cell. Its mask is every cell the live island covers: 5 036 of 10 080, read from the map's edge picture (`oar/norr-kant.png`, 8 px per cell).
- The urban area rule gives 6 247 cells here, 2 250 of them off the island, and it misses 1 039 cells that are on it. Lidingö is its own urban area but sits on the island. A cell off the island would also need a new edge picture.
- Google covers the whole sheet, so the photo cover rule does not apply.

### A Google source per sheet
- Google stopped serving Photorealistic 3D Tiles to EEA-billed Map Tiles projects after 2025-07-08, but Cesium ion still streams them as asset 2275207, and a free ion account works from Sweden: about 1 000 root loads a month.
- A wrong token comes back from ion as 401 on `/v1/assets/2275207/endpoint` within five seconds, before any tile loads.
- Chrome, Cesium and the Chrome download come to about 750 MB, kept out of the Python package in `cli/google-render`.

### The service is live
- `pixelbygget.gustaf-garnow.workers.dev` runs with D1, R2 and KV in Western Europe. Only `goteborg-norr` is open; `stockholm-norr` opened on 2026-11-22 with its Google source and 5 036 cells.
- A municipal orthophoto stops at the municipal border. Göteborg's is pure white over Partille, Mölndal and Kungälv, which lie inside the Göteborg urban area. A sheet's mask therefore needs both rules: the urban area plus 500 m, and full photo cover over the cell's whole photo box. Göteborg norr: 5 349 cells by area, 3 071 with photo.
- overpass-api.de answers 406 to curl's default User-Agent. With `pixelbygget-rutor/0.1` a small query took 28 s, and 42 s with the pinned date: history queries are slower.

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
- A second, fresh review of the service fixes found four more races in them: a purged key could be a re-upload with the same bytes (the seed is fixed per cell), the cron could free a lock taken over after it looked, two `/live` calls with the same rev could both move cells, and a reject could free a cell that had just gone live. Each write now names the exact state it read, keys carry a random part, and each race has a test that fails on the code before it. Review your own fixes with fresh eyes too.
- CRC32 in JavaScript costs about 14 ms of CPU for 8 MB, over the Workers Free limit of 10 ms. The upload checks every chunk's CRC except the image data's.

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
