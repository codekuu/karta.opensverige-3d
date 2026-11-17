# Pixelbygget

Pixelbygget is the volunteer grid for the OpenSverige map. A city is a sheet of cells. You claim one cell from the service, render it on your own machine, redraw it as pixel art, and upload the PNG. The service keeps the lock, the review queue and the address of the cell on the map.

This repository is the `rutor` tool and the pinned redraw pipeline. You do not push claims or tiles with git. The PNG you upload is the only thing that leaves your machine, and it is the redraw, not the source render.

Run the tool from `cli/` with `python -m rutor`, or install it into the virtualenv with `python -m pip install -e cli` and then call `rutor`. Output lines look like `claimed: goteborg-norr 64_59 expires 2026-11-19T12:00:00Z`. An agent reads [AGENTS.md](AGENTS.md) before it runs anything. The rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## The five steps

```bash
rutor login --accept-terms
rutor claim goteborg-norr --next
rutor render goteborg-norr 64_59
rutor redraw goteborg-norr 64_59
rutor upload goteborg-norr 64_59
```

`rutor claim` prints the cell you got. Use that `col_row` in render, redraw and upload. `rutor login` prints a device code. Approve it in the browser at the URL it prints. That approval, and putting your own Google key in `.env`, are the two steps that need a person.

`rutor redraw --wait-for-cheap` runs the SE3 price check first and does not redraw while the price is high. Without the flag the price check is advice only: `python -m rutor.se3_price`.

`rutor preflight` checks Python, PyTorch with CUDA, bitsandbytes, Chrome, the key, the service and the login. Do not render or redraw when any line says `missing`.

## Setup

Use Python 3.11 or newer. NVIDIA CUDA is required for the redraw. The 4-bit checkpoint is a bitsandbytes quant. The redraw is aimed at 24 GB cards. On the maintainer's RTX 5070 one cell takes about an hour.

Install a CUDA build of PyTorch first, then the rest, so a later install does not replace it with a CPU wheel. If the CUDA 12.8 index does not match your driver, use the current index from <https://pytorch.org>.

Model weights are not in this repo. The first redraw downloads them into the Hugging Face cache on your machine, on the order of 20 GB.

- `addlabsviral/Qwen-Image-2.1-Turbo-4bit`
- LoRA `handful/mndf-pixel-qwen-image-2.1`

The Qwen Research License applies to those weights. They are not copied into this repo.

### Windows (PowerShell)

```powershell
git clone https://github.com/Baltsar/karta.opensverige-3d
cd karta.opensverige-3d
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
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
git clone https://github.com/Baltsar/karta.opensverige-3d
cd karta.opensverige-3d
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
python -m pip install -e cli
rutor preflight
```

### macOS

```bash
git clone https://github.com/Baltsar/karta.opensverige-3d
cd karta.opensverige-3d
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch
python -m pip install -r requirements.txt
python -m pip install -e cli
rutor preflight
```

A Mac can install, log in, claim and check the SE3 price. Preflight reports CUDA missing, and the redraw does not run there.

Without `pip install -e cli`, the same commands work as `python -m rutor` from the `cli/` directory.

## Google key

You render with your own Google account, your own key and your own agreement with Google. Put the key in `.env` in the clone:

```bash
echo 'GOOGLE_MAPS_KEY=your-key' > .env
chmod 600 .env
```

The key stays in `.env`. The CLI reads it for `rutor render` and refuses a `.env` that is group- or world-readable on POSIX. The source render (`work/<sheet>/<col>_<row>.src.png`) stays on your machine. Nothing Google-derived is uploaded. `rutor upload` sends only the redrawn PNG.

## Licence and credit

The PNG you upload is contributed under the MIT license. See [LICENSE](LICENSE). It may be shown on karta.opensverige.se. You are credited by your GitHub login. The model weights stay under their own licenses.

## Local checks

This does not download the model:

```bash
cd cli && python3 -m unittest discover -s tests -v
```
