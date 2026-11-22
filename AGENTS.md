# Agents

There are two kinds of work here. Find out which one your human wants before you run anything.

- **A code change.** Open now. You edit the CLI, the renderer, the check, the service or the docs, and open a pull request.
- **A GPU run.** You help one person add one map cell from their own NVIDIA GPU.

The commands are in [README.md](README.md), the rules in [CONTRIBUTING.md](CONTRIBUTING.md), and how the parts fit together in [docs/pixelbygget/DESIGN.md](docs/pixelbygget/DESIGN.md).

## A code change

1. Branch from `main`.
2. Install the CLI with its render extra: `python -m pip install -e "cli[render]"`. No GPU is needed.
3. Make the change with a test. A renderer change must keep `test_neighbour_cells_agree_in_their_overlap` at zero differing pixels.
4. Run both suites from the repository root:
   - `cd cli && python -m unittest discover -s tests`
   - `cd service && npm ci && npm test` (Node 22 or newer)
5. Write what you learned in `docs/pixelbygget/LEARNINGS.md`, newest first.
6. Open a pull request against `main`.

Do not loosen the quality check in `cli/rutor/check.py` to make a test pass. Do not commit images, model weights, tokens or keys.

To try `rutor render` against a real service, run one locally with `service/scripts/local.sh`. It needs no GitHub app and no real token.

## A GPU run

Run `rutor` from a virtualenv where `pip install -e cli` has been done, or run `python -m rutor` from `cli/`.

1. `rutor login` (see the human steps below)
2. `rutor preflight`
3. `rutor claim <sheet> --next`
4. `rutor render <sheet> <col>_<row>`
5. `rutor redraw <sheet> <col>_<row>`
6. `rutor upload <sheet> <col>_<row>`

Use the cell the claim prints. Preflight checks the login too, so it comes after it.

`rutor redraw --wait-for-cheap` runs the SE3 price check first. It exits 6 when the price is high: keep the lock and run it again after the time it prints. Without that flag, `python -m rutor.se3_price` is advice, not a gate.

### The human steps

Only these need a person. Do not invent a way around them.

- Read the terms `rutor login` prints and decide to accept them. Pass `--accept-terms` only after your human said yes.
- Approve the GitHub device login in the browser, at the URL `rutor login` prints.
- On a Google sheet (`stockholm-norr`), make the Cesium ion account and its token. Do not make an account for your human. They put the token in `CESIUM_ION_TOKEN` themselves.

The source render comes from the sheet's source. `goteborg-norr` is open data: Göteborgs Stad orthophoto (CC0) and OpenStreetMap (ODbL). A Google sheet needs Node 22 and `npm ci` once in `cli/google-render`; `rutor render` prints what Google's terms mean for the cell.

### Stop and tell your human

- Preflight prints any `check: <name> missing` line. Do not claim, render or redraw.
- Preflight says there is no CUDA GPU, for example on a Mac. Do not claim a cell on that machine.
- `rutor render` says `CESIUM_ION_TOKEN is not set` or `Cesium ion refused the token`. Ask your human for a token. Do not look for one.
- `rutor render` says the Cesium ion quota is used up. Release the cell, or try again tomorrow.
- The service refuses the same step twice. Report the `error:` line. Do not loop.
- Exit 4: the network failed. Try once more later, then report it.
- The redraw fails. Report the last lines. Do not change the redraw to get past it.
- `rutor redraw` or `rutor check` prints `check:` lines and exits 5. The cell failed the quality check. Do not upload it and do not loosen the check. Report the `check:` lines.

### The redraw is long

One cell takes about two minutes on a 12 GB card, plus the model download the first time. The redraw prints `encode window i/n` while it works.

- Run it once and wait. Do not start a second run.
- Do not treat a quiet minute as a hang.
- The first run downloads about 20 GB of weights before it prints a window.

### Never

- Never upload `work/<sheet>/<col>_<row>.src.png`. `rutor upload` sends the redrawn PNG only. Do not PUT the source render yourself.
- Never edit the manifest by hand. `rutor upload` refuses a changed PNG or source, and the service refuses a manifest that has not passed the check.
- Never claim a second cell while one lock is still open, unless `rutor whoami` prints `trusted: yes`.
- Never pass `--wait-for-cheap` and then ignore a non-zero exit from it.

### When you cannot finish

Give the cell back, then say so:

```bash
rutor release <sheet> <col>_<row>
```

### Report when done

Say which sheet and cell, the `uploaded:` line, and the `url:` line.
