# Pixelbygget

Pixelbygget is the volunteer grid for the OpenSverige map. A city is a sheet of cells. You claim one cell from the service, render it on your own machine, redraw it as pixel art, and upload the PNG. The service keeps the lock, the review queue and the address of the cell on the map.

## Status

Code contributions are open now: the renderer, the open-data sources, the quality check and the docs. GPU contributions are open too, on `goteborg-norr` for now.

Start with an issue labelled `good first issue`. Open a pull request against `main`. The tests must pass. How the parts fit together is in [docs/pixelbygget/DESIGN.md](docs/pixelbygget/DESIGN.md).

A code change needs no GPU:

```bash
python -m pip install -e "cli[render]"
```

To try `rutor render` against a real service, run one on your machine with `service/scripts/local.sh`. It needs no GitHub app and no real token.

## What this is

This repository is the `rutor` tool and the pinned redraw pipeline. You do not push claims or tiles with git. The PNG you upload is the only thing that leaves your machine, and it is the redraw, not the source render.

Run the tool from `cli/` with `python -m rutor`, or install it into the virtualenv with `python -m pip install -e cli` and then call `rutor`. Output lines look like `claimed: goteborg-norr 64_59 expires 2026-11-19T12:00:00Z`. An agent reads [AGENTS.md](AGENTS.md) before it runs anything. The rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## The steps

```bash
rutor preflight --machine     # python, the NVIDIA card and the disk: before anything else
rutor login                   # prints the terms and asks you to accept them
rutor preflight               # everything, once the install and the login are done
rutor claim goteborg-norr --next
rutor render goteborg-norr 64_59
rutor redraw goteborg-norr 64_59
rutor upload goteborg-norr 64_59
```

`rutor preflight --machine` needs nothing but the light install (`pip install -e cli`) and no login, so run it first: it says whether this machine can build a cell at all. `rutor claim` refuses on a machine that cannot.

`rutor login` prints the terms ([TERMS.md](TERMS.md)) and a device code. `--accept-terms` answers yes for you, so pass it only when you have read them. Approve the code in the browser at the URL it prints. That approval is the step that needs a person.

`rutor claim` prints the cell you got and a `url:` line: the cell on the map, with your login on it within a minute. Use that `col_row` in render, redraw and upload. `rutor status` shows your open locks, `rutor whoami` your login and whether you are trusted.

`rutor redraw --wait-for-cheap` runs the SE3 price check first. While the price is high it exits 6 and you keep your lock; run it again after the time it prints. Without the flag the price check is advice only: `python -m rutor.se3_price`.

`rutor preflight` checks Python, the NVIDIA card, the disk, PyTorch with CUDA, bitsandbytes, Pillow and numpy, the service and the login. Do not claim, render or redraw when any line says `missing`.

Exit codes: 0 done, 1 bad input, 2 refused by the service, 3 something missing on this machine, 4 network, 5 the redraw failed the quality check, 6 the SE3 price is high (try later).

## Setup

Use Python 3.11 or newer. NVIDIA CUDA is required for the redraw. The 4-bit checkpoint is a bitsandbytes quant. A 12 GB card is enough: the redraw peaks at about 11 GB. One cell takes about two minutes on an RTX 5070 or an L4.

Install a CUDA build of PyTorch and torchvision first, then the rest, so a later install does not replace them with CPU wheels. The redraw does not load without torchvision. If the CUDA 12.8 index does not match your driver, use the current index from <https://pytorch.org>.

Model weights are not in this repo. The first redraw downloads them into the Hugging Face cache on your machine, on the order of 20 GB.

- `addlabsviral/Qwen-Image-2.1-Turbo-4bit`
- LoRA `handful/mndf-pixel-qwen-image-2.1`

The Qwen Research License applies to those weights. They are not copied into this repo.

### Windows (PowerShell)

```powershell
git clone https://github.com/opensverige/karta.opensverige-3d
cd karta.opensverige-3d
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
python -m pip install -e cli
rutor preflight
```

If activation is blocked for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux

```bash
git clone https://github.com/opensverige/karta.opensverige-3d
cd karta.opensverige-3d
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
python -m pip install -e cli
rutor preflight
```

### macOS

```bash
git clone https://github.com/opensverige/karta.opensverige-3d
cd karta.opensverige-3d
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision
python -m pip install -r requirements.txt
python -m pip install -e cli
rutor preflight
```

A Mac can install, log in, check the SE3 price and run the tests. Preflight reports CUDA missing and the redraw does not run there, so do not claim a cell on a Mac.

Without `pip install -e cli`, the same commands work as `python -m rutor` from the `cli/` directory.

## Source render

Each sheet has one source, so neighbouring cells match:

- `goteborg-norr` is drawn from open data. Göteborgs Stad orthophoto and terrain model are CC0. OpenStreetMap is ODbL. The first render in an area also reads about 7 MB of terrain per square kilometre.
- `stockholm-norr` is drawn from Google Photorealistic 3D Tiles through Cesium ion. It is open, and it asks more of you: read line 10 of [TERMS.md](TERMS.md) first.

A Google sheet needs three things on your machine:

- A free account at [ion.cesium.com](https://ion.cesium.com) and a token with the default scopes, in `CESIUM_ION_TOKEN`. A free account covers about 1 000 cells a month.
- Node 22 or newer.
- `npm ci` once in `cli/google-render`. It downloads Cesium and a Chrome for headless rendering, about 750 MB.

Google's terms bind your token, not this project's MIT license. Google's Map Tiles policy does not allow deriving new content from its tiles, so read it and decide for yourself. Google can ask for cells drawn from its tiles to be taken down. `rutor render` prints this on every Google cell, and the manifest names the source.

The source render (`work/<sheet>/<col>_<row>.src.png`) stays on your machine. `rutor upload` sends only the redrawn PNG.

## Licence and credit

Building a cell is voluntary and unpaid. What you accept at `rutor login` is ten lines in [TERMS.md](TERMS.md): what leaves your machine, who reviews it, that your GitHub login is shown, and which licenses the model and the sources carry. The code is MIT, see [LICENSE](LICENSE).

## Local checks

This does not download the model:

```bash
cd cli && python -m unittest discover -s tests -v
```

The renderer tests need Pillow and numpy; without them they are skipped, not passed. The service tests need Node 22 or newer:

```bash
cd service && npm ci && npm test
```
