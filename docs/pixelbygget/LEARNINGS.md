# Pixelbygget learnings

What we tried, what we measured and what we changed. Newest first. One entry per finding, with the number that decided it. Images, renders and raw logs stay outside the repository; this file keeps the conclusions.

## 2026-11-19

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
- Gustaf's uplink measured about 20 kB/s. Render sources on the box from the open WMS, upload only code and OSM extracts (a few MB, gzipped).
- The `pytorch/pytorch:2.8.0` image ships old `brotli` and `zstandard`; Hugging Face downloads then crash in `httpx2` with `process() takes no keyword arguments`. Fix: `pip install -U brotli zstandard`.
- Start long jobs with `setsid nohup ... &` and `ssh -n`, or the job dies with the SSH session. Vast boxes can refuse the direct port for minutes while they install SSH; the `sshN.vast.ai` proxy address may also lag.
- `pgrep -f pattern` over SSH matches the SSH command line itself. Grep the process list for the script name instead.
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
