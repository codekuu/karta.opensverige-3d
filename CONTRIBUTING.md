# Contributing

You lend one GPU run. The map gets one cell, credited to your GitHub login.

This page holds the rules. The commands and the install are in [README.md](README.md). If an agent runs the commands for you, it reads [AGENTS.md](AGENTS.md) first.

## Before you start

- An NVIDIA GPU with CUDA and 12 GB of memory or more.
- Time. One cell takes about two minutes on an RTX 5070 or an L4.
- About 20 GB of free disk for the model download.
- Windows or Linux for the redraw. A Mac can log in, claim and check the price. It cannot redraw.
- Python 3.11 or newer, and a GitHub account.
- The source render comes from open data. Göteborgs Stad orthophoto is CC0. OpenStreetMap is ODbL. Only `goteborg-norr` has a source so far.

Clone, install, and run `rutor preflight`. Go on only when every line says `ok`.

## The run

```bash
rutor login --accept-terms
rutor claim <sheet> --next
rutor render <sheet> <col>_<row>
rutor redraw <sheet> <col>_<row>
rutor upload <sheet> <col>_<row>
```

Each step is explained in [README.md](README.md).

## Rules

- One cell at a time until you are trusted. Finish it or release it before you take another. Trust arrives after ten approved cells. Until then a second open lock is refused.
- The first ten cells are reviewed. `rutor upload` leaves a new contributor's cell waiting. An admin approves or rejects it.
- Do not send the source render to anyone. `work/<sheet>/<col>_<row>.src.png` stays on your machine. Only the redrawn PNG is uploaded.
- Do not edit `cli/rutor/pipeline.py`, the redraw, or another contributor's cell. Found a bug? Open an issue.
- Cannot finish? Give the cell back: `rutor release <sheet> <col>_<row>`.

## Code contributions

Code contributions are open now. GPU runs open when the service is live.

- Pick an issue, or open one before a large change.
- Branch from `main` and open a pull request against `main`.
- Run the tests before you push: `cd cli && python3 -m unittest discover -s tests` and `cd service && npm test`.
- A source render must stay identical between neighbouring cells. Add a test when you change `cli/rutor/renderer/`.
- Write what you learned in `docs/pixelbygget/LEARNINGS.md`, newest first.
- Never commit images, model weights or keys.

## How to report

Open an issue on <https://github.com/opensverige/karta.opensverige-3d/issues>. Include the command, the exit code and the `error:` line. Do not paste your GitHub token.

## Licence and credit

- The PNG you upload is contributed under the MIT license. See [LICENSE](LICENSE).
- By uploading, you agree that it may be shown on karta.opensverige.se.
- You are credited by the GitHub login you used in `rutor login`.
- Contribute only a cell you made with this pipeline, from the open-data source render.
- The model weights stay under their own licenses. The Qwen Research License grants non-commercial use only.
