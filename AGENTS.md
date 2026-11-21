# Agents

You are helping one person add one map cell from their own GPU. Read [README.md](README.md) and follow its steps in order. The rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

Run `rutor` from a virtualenv where `pip install -e cli` has been done, or run `python -m rutor` from `cli/`.

## Order

1. `rutor preflight`
2. `rutor login` (the human approves the device code in the browser)
3. `rutor claim <sheet> --next`
4. `rutor render <sheet> <col>_<row>`
5. `rutor redraw <sheet> <col>_<row>`
6. `rutor upload <sheet> <col>_<row>`

Use the cell the claim prints. `rutor redraw --wait-for-cheap` runs the SE3 price check first and stops when the price is high. Without that flag, `python -m rutor.se3_price` is advice, not a gate.

## The human step

Only this needs a person. Do not invent a way around it.

- Approve the GitHub device login in the browser, at the URL `rutor login` prints.

The source render comes from open data: Göteborgs Stad orthophoto (CC0) and OpenStreetMap (ODbL). Only `goteborg-norr` has a source so far.

## Stop and tell your human

- Preflight prints any `check: <name> missing` line. Do not render or redraw.
- `rutor login` refuses because the GitHub client id is still the placeholder.
- The service refuses the same step twice. Report the `error:` line. Do not loop.
- The redraw fails. Report the last lines. Do not change `cli/rutor/pipeline.py` or the redraw to get past it.
- `rutor redraw` or `rutor check` prints `check:` lines and exits 5. The cell failed the quality check. Do not upload it and do not loosen the check. Report the `check:` lines.

## The redraw is long

One cell takes about two minutes on a 12 GB card, plus the model download the first time. The redraw prints `encode window i/n` while it works.

- Run it once and wait. Do not start a second run.
- Do not treat a quiet minute as a hang.
- The first run downloads about 20 GB of weights before it prints a window.

## Never

- Never upload `work/<sheet>/<col>_<row>.src.png`. `rutor upload` sends the redrawn PNG only. Do not PUT the source render yourself.
- Never edit `cli/rutor/pipeline.py`.
- Never claim a second cell while one lock is still open, unless `rutor whoami` shows a trusted login.
- Never pass `--wait-for-cheap` and then ignore a non-zero exit from it.

## When you cannot finish

Give the cell back, then say so:

```bash
rutor release <sheet> <col>_<row>
```

## Report when done

Say which sheet and cell, the `uploaded:` line, and the `url:` line.
