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
- A connection that can download about 16 GB once: 12 for the model, 3 for PyTorch and its libraries, 1 for the `stockholm-norr` renderer.

**Programs**

- Python 3.11 or newer.
- Git.
- Node 22 or newer, for `stockholm-norr`. That is the only sheet with free cells today.

**Accounts, all free**

- GitHub. You log in with it, and your login is shown on your cells.
- Cesium ion, for `stockholm-norr`. A free account covers about 1 000 cells a month.
- Lantmäteriet. Not needed yet. It comes when new towns open, for the national orthophoto.

**What it costs you**

- The first time: the install and about 12 GB of model weights.
- For each cell: the redraw takes about two minutes on an RTX 5070, and the source render a few minutes more.
- The electricity is yours. `rutor redraw --wait-for-cheap` checks the price first and stops when it is high, so you can run it again in a cheap hour.

**The limits**

- One cell at a time. A lock is yours for 48 hours, then the cell is free again.
- At most five of your cells wait for review at once.
- At most 20 uploads a day as a new builder.
- A person reviews every cell. It can be rejected, or taken down later.

**What you agree to**, in ten lines in [TERMS.md](TERMS.md):

- It is voluntary and unpaid, at your own risk.
- Only the finished picture and its short manifest leave your machine.
- The redraw model has a research license that does not allow commercial use.
- `stockholm-norr` is drawn from Google's 3D tiles through your Cesium account. Google's policy does not allow that. You decide for yourself.

**Memory:** the redraw holds about 13 GB of the machine's ordinary memory at its peak, measured on a machine with 32 GB. A machine with 16 GB has not been tried. `rutor app` warns below 17 GB and does not refuse.

The quickest way to know whether a machine will do is `rutor preflight --machine`. It needs no login and answers in seconds.

## What this is

This repository is the `rutor` tool and the pinned redraw pipeline. You do not push claims or tiles with git. The PNG you upload and its manifest are the only things that leave your machine. The PNG is the redraw, not the source render, and the manifest says how it was made: the pipeline, the source and the name of your graphics card.

Run the tool from `cli/` with `python -m rutor`, or install it into the virtualenv with `python -m pip install -e cli` and then call `rutor`. Output lines look like `claimed: stockholm-norr 74_20 expires 2026-11-19T12:00:00Z`. An agent reads [AGENTS.md](AGENTS.md) before it runs anything. The rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## The steps

```bash
rutor preflight --machine     # python, the NVIDIA card and the disk: before anything else
rutor login                   # prints the terms and asks you to accept them
rutor preflight --sheet stockholm-norr   # everything, once the install and the login are done
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

Exit codes: 0 done, 1 bad input, 2 refused by the service, 3 something missing on this machine, 4 network, or a service that is busy or down (try again in a minute), 5 the redraw failed the quality check, 6 the SE3 price is high (try later).

## Build from a page

`rutor app` does the steps above from a page on your own machine, so that after the install you need no terminal:

```bash
rutor app        # or: rutor bygg
```

It prints `url:` with the page's address, opens it in your browser, and runs until you press Ctrl-C. The page is in Swedish and takes you through eight steps, one question at a time:

1. It looks at the machine itself: the NVIDIA card, the free disk and the memory. A machine that cannot draw gets a plain no here, and one with less than 17 GB of memory a warning.
2. The ten terms ([TERMS.md](TERMS.md)), word for word, and a box to tick.
3. The GitHub login: the page shows the device code and you approve it at GitHub.
4. Your Cesium ion token, for a sheet that needs one. You paste it once. Cesium ion is asked whether it works before it is kept.
5. What is missing of PyTorch, bitsandbytes, Pillow, Node and the Google renderer. Into an install made by hand the page installs nothing: see [Setup](#setup). The Windows app (`installer/`) fetches them itself here, about 16 GB, with a progress bar.
6. The sheet's cells, and a button that picks the next free one.
7. The page asks before the card is used, shows the SE3 price, and then runs claim, render, redraw and upload for that one cell.
8. The cell waits for review, with its address on the map.

What is worth knowing:

- **The page drives the same commands.** It starts `rutor claim`, `render`, `redraw` and `upload` as its own child processes and reads the lines they print. There is one drawing code, not two.
- **The cell is locked when you press Rita,** not when you choose it. One cell at a time.
- **Stopping gives the lock back.** Avbryt ends the running command and what it started, Node and Chrome too, and releases the cell. So does Ctrl-C. The one exception is the last few seconds: a stop that comes while the picture is being sent waits for the sending to end, and the cell then waits for review. A picture that is half sent may already be with the service.
- **A closed tab stops the job too,** after two and a half minutes without the page asking. A tab you have only switched away from keeps asking and keeps the job.
- **Nothing runs when the page is closed.** No scheduled job, no service, no background loop.
- **The page is for you alone.** It listens on 127.0.0.1, under an address with a long random key that is new at every start, and loads nothing from outside. Whoever has that address can build cells as you while the page is up, so keep it to yourself.
- **The browser page never sees your login or your Cesium token.** Neither is in anything the page is sent. The login goes to the project's service with each command, as from the terminal, and the Cesium token to Cesium ion. A token pasted into the page is kept beside the login (`cesium-token`, readable by you only) and is the one the page uses, also when `CESIUM_ION_TOKEN` is set.
- **When a cell fails,** "Kopiera felrapport" copies a report with your user name and home folder taken out. You paste it into an issue yourself. The page sends nothing on its own.

The page says no on a Mac and on a machine without a 12 GB NVIDIA card, as `rutor claim` does. From a clone the install is still done by hand, as under [Setup](#setup).

If the app was ended in the middle of a cell, that cell is still locked when you come back. The page finds it the next time you press Rita, says so, and gives it back when you press again.

The first cell built this way was `stockholm-norr` 82_24, on an RTX 5070 on Windows: about 2 minutes 45 seconds from Rita to waiting for review.

## Setup

Use Python 3.11 or newer. NVIDIA CUDA is required for the redraw. The 4-bit checkpoint is a bitsandbytes quant. A 12 GB card is enough: the redraw peaks at about 11 GB. One cell takes about two minutes on an RTX 5070 or an L4.

Check the machine before anything large is fetched. Then install a CUDA build of PyTorch and torchvision before `requirements.txt`, so a later install does not replace them with CPU wheels. The redraw does not load without torchvision. If the CUDA 12.8 index does not match your driver, use the current index from <https://pytorch.org>.

Model weights are not in this repo. The first redraw downloads them into the Hugging Face cache on your machine, about 12 GB.

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
python -m pip install -e cli
rutor preflight --machine
```

Go on only when all three lines say `ok`. The rest downloads about 3 GB:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
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
python -m pip install -e cli
rutor preflight --machine
```

Go on only when all three lines say `ok`. The rest downloads about 3 GB:

```bash
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
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

- `goteborg-norr` is drawn from open data. Göteborgs Stad orthophoto and terrain model are CC0. OpenStreetMap is ODbL. The first render in an area also reads about 7 MB of terrain per square kilometre.
- `goteborg-norr` has no free cells today. The built-up ground the map is missing there lies beyond Göteborgs Stad's orthophoto, in the neighbouring municipalities, and the renderer has no other open photo yet.
- `stockholm-norr` is drawn from Google Photorealistic 3D Tiles through Cesium ion. It is open, and it asks more of you: read point 10 of [TERMS.md](TERMS.md) first.

A Google sheet needs three things on your machine:

- A free account at [ion.cesium.com](https://ion.cesium.com) and a token with the default scopes (Access Tokens in ion's menu), in `CESIUM_ION_TOKEN`. A free account covers about 1 000 cells a month.
  - Windows: `setx CESIUM_ION_TOKEN "<your token>"` in PowerShell, then close and reopen the window. A variable set in one window does not reach another that was already open.
  - Linux: `export CESIUM_ION_TOKEN=<your token>` in the shell you run `rutor` in.
  - `rutor app` asks for the token on its page instead, and keeps it beside the login.
- Node 22 or newer.
- `npm ci` once in `cli/google-render`. It downloads Cesium and a Chrome for headless rendering, about 1 GB.

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
