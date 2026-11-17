# rutor

`rutor` is the Pixelbygget command. It logs in with GitHub, claims a cell, renders and redraws it, and uploads the PNG. The service contract and the full install, including CUDA, are in the repository [README](../README.md). The rules are in [CONTRIBUTING](../CONTRIBUTING.md).

Python 3.11 or newer. The client itself uses the standard library. The redraw needs the CUDA stack in the repository `requirements.txt`.

## Install

From the repository root, inside the virtualenv from the main README:

```bash
python -m pip install -e cli
rutor --version
```

Or, without installing, from this directory:

```bash
python -m rutor --version
```

## The five steps

```bash
rutor login --accept-terms
rutor claim goteborg-norr --next
rutor render goteborg-norr 64_59
rutor redraw goteborg-norr 64_59
rutor upload goteborg-norr 64_59
```

Replace `64_59` with the cell `rutor claim` prints. `rutor preflight` should be `ok` on every line before `rutor render`.

`rutor login` prints a code and `url: https://github.com/login/device`. A person approves that code in the browser. The token is stored in `~/.config/pixelbygget/token` (override with `PIXELBYGGET_HOME`), mode `0600`.

The Google key stays in `.env` at the clone root. `rutor render` refuses the file when it is group- or world-readable.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
