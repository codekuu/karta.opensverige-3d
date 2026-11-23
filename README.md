# Pixelbygget

Pixelbygget is the volunteer grid for the OpenSverige map. A city is a sheet of cells. You claim one cell from the service, render it on your own machine, redraw it as pixel art, and upload the PNG. The service keeps the lock, the review queue and the address of the cell on the map.

## Status

Code contributions are open now: the renderer, the open-data sources, the quality check and the docs. GPU contributions are open too. Volunteers draw what the map is missing: a free cell is built-up ground the live map has not drawn yet, and a cell that is already on the map cannot be taken. Today the free cells are on `stockholm-norr`.

Start with an issue labelled `good first issue`. Open a pull request against `main`. The tests must pass. How the parts fit together is in [docs/pixelbygget/DESIGN.md](docs/pixelbygget/DESIGN.md).

A code change needs no GPU:

```bash
python -m pip install -e "cli[render]"
```

To try `rutor render` against a real service, run one on your machine with `service/scripts/local.sh`. It needs no GitHub app and no real token.

## What you need

Read this before you start. It is what building a cell asks of you and your machine.

**The machine**

- An NVIDIA card with 12 GB of memory. AMD and Intel cards do not work, and neither does a Mac.
- Windows or Linux.
- 25 GB of free disk, and about 1 GB more for `stockholm-norr`.
- A connection that can download about 20 GB once.

**Programs**

- Python 3.11 or newer.
- Git.
- Node 22 or newer, for `stockholm-norr`. That is the only sheet with free cells today.

**Accounts, all free**

- GitHub. You log in with it, and your login is shown on your cells.
- Cesium ion, for `stockholm-norr`. A free account covers about 1 000 cells a month.
- Lantmäteriet. Not needed yet. It comes when new towns open, for the national orthophoto.

**What it costs you**

- The first time: the install and about 20 GB of model weights.
- For each cell: the redraw takes about two minutes on an RTX 5070, and the source render a few minutes more.
- The electricity is yours. `rutor redraw --wait-for-cheap` checks the price first and stops when it is high, so you can run it again in a cheap hour.

**The limits**

- One cell at a time. A lock is yours for 48 hours, then the cell is free again.
- At most five of your cells wait for review at once.
- At most 20 uploads a day as a new builder.
- A person reviews every cell. It can be rejected, or taken down later.

**What you agree to**, in ten lines in [TERMS.md](TERMS.md):

- It is voluntary and unpaid, at your own risk.
- Only the finished picture leaves your machine.
- The redraw model has a research license that does not allow commercial use.
- `stockholm-norr` is drawn from Google's 3D tiles through your Cesium account. Google's policy does not allow that. You decide for yourself.

**Not measured:** how much ordinary memory the machine needs. The test machine has 32 GB.

The quickest way to know whether a machine will do is `rutor preflight --machine`. It needs no login and answers in seconds.

## What this is

This repository is the `rutor` tool and the pinned redraw pipeline. You do not push claims or tiles with git. The PNG you upload is the only thing that leaves your machine, and it is the redraw, not the source render.

Run the tool from `cli/` with `python -m rutor`, or install it into the virtualenv with `python -m pip install -e cli` and then call `rutor`. Output lines look like `claimed: stockholm-norr 74_20 expires 2026-11-19T12:00:00Z`. An agent reads [AGENTS.md](AGENTS.md) before it runs anything. The rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## The steps

```bash
rutor preflight --machine     # python, the NVIDIA card and the disk: before anything else
rutor login                   # prints the terms and asks you to accept them
rutor preflight               # everything, once the install and the login are done
rutor claim stockholm-norr --next
rutor render stockholm-norr 74_20
rutor redraw stockholm-norr 74_20
rutor upload stockholm-norr 74_20
```

`rutor preflight --machine` needs nothing but the light install (`pip install -e cli`) and no login, so run it first: it says whether this machine can build a cell at all. `rutor claim` refuses on a machine that cannot.

`rutor login` prints the terms ([TERMS.md](TERMS.md)), a device code, and then waits until the code is approved. `--accept-terms` answers yes for you, so pass it only when you have read them. Approve the code in the browser at the URL it prints. That approval is the step that needs a person.

Today's free cells are on `stockholm-norr`, which needs Node 22, `npm ci` in `cli/google-render` and your own Cesium ion token: see [Source render](#source-render). Do that before you claim. `rutor preflight --sheet stockholm-norr` checks it.

`rutor claim` prints the cell you got and a `url:` line: the cell on the map, with your login on it within a minute. Use that `col_row` in render, redraw and upload. `rutor status` shows your open locks, `rutor whoami` your login and whether you are trusted.

`rutor redraw --wait-for-cheap` runs the SE3 price check first. While the price is high it exits 6 and you keep your lock; run it again after the time it prints. Without the flag the price check is advice only: `python -m rutor.se3_price`.

`rutor preflight` checks Python, the NVIDIA card, the disk, PyTorch with CUDA, bitsandbytes, Pillow and numpy, the service and the login. With `--sheet stockholm-norr` it also checks what that sheet's source needs: Node, the Google renderer and the Cesium ion token. Do not claim, render or redraw when any line says `missing`. `rutor claim` refuses on a Google sheet until those three are in place, so no cell is locked that the machine cannot render.

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
rutor preflight --machine
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
rutor preflight --machine
```

### macOS

A Mac cannot build a cell: the redraw needs an NVIDIA card. It can change the code and run the tests, and that needs neither the model stack nor a login:

```bash
git clone https://github.com/opensverige/karta.opensverige-3d
cd karta.opensverige-3d
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "cli[render]"
rutor preflight --machine
cd cli && python -m unittest discover -s tests
```

`rutor preflight --machine` says `check: gpu missing` there. Do not claim a cell on a Mac. `rutor claim` refuses anyway.

Without `pip install -e cli`, the same commands work as `python -m rutor` from the `cli/` directory.

## Source render

Each sheet has one source, so neighbouring cells match:

- `goteborg-norr` has no free cells today. The built-up ground the map is missing there lies beyond Göteborgs Stad's orthophoto, in the neighbouring municipalities, and the renderer has no other open photo yet.
- `goteborg-norr` is drawn from open data. Göteborgs Stad orthophoto and terrain model are CC0. OpenStreetMap is ODbL. The first render in an area also reads about 7 MB of terrain per square kilometre.
- `stockholm-norr` is drawn from Google Photorealistic 3D Tiles through Cesium ion. It is open, and it asks more of you: read point 10 of [TERMS.md](TERMS.md) first.

A Google sheet needs three things on your machine:

- A free account at [ion.cesium.com](https://ion.cesium.com) and a token with the default scopes, in `CESIUM_ION_TOKEN`. A free account covers about 1 000 cells a month.
- Node 22 or newer.
- `npm ci` once in `cli/google-render`. It downloads Cesium and a Chrome for headless rendering, about 750 MB.

Google's terms bind your token, not this project's MIT license. Google's Map Tiles policy does not allow deriving new content from its tiles, so read it and decide for yourself. Google can ask for cells drawn from its tiles to be taken down. `rutor render` prints a notice on every Google cell, and the manifest names the source.

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
