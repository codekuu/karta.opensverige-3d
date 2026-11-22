# rutor

`rutor` is the Pixelbygget command. It logs in with GitHub, claims a cell, renders and redraws it, and uploads the PNG. The full install, including CUDA, is in the repository [README](../README.md). The service API is in [service/README.md](../service/README.md). The rules are in [CONTRIBUTING](../CONTRIBUTING.md).

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

## The steps

```bash
rutor login --accept-terms
rutor preflight
rutor claim goteborg-norr --next
rutor render goteborg-norr 64_59
rutor redraw goteborg-norr 64_59
rutor upload goteborg-norr 64_59
```

Replace `64_59` with the cell `rutor claim` prints. `rutor preflight` checks the login too, so run it after `rutor login`, and it should be `ok` on every line before `rutor claim`.

`rutor login` prints a code and `url: https://github.com/login/device`. A person approves that code in the browser. The token is stored in `~/.config/pixelbygget/token` (override with `PIXELBYGGET_HOME`), mode `0600` on Linux and macOS. `PIXELBYGGET_URL` points the CLI at another service, such as a local one. That approval is the step that needs a person.

`rutor render` builds the source render from the sheet's source: open data on `goteborg-norr`, Google's tiles through Cesium ion on `stockholm-norr` (see the main README for the token and `npm ci` in `cli/google-render`). The open-data path needs Pillow and numpy, which are in the repository `requirements.txt` and in the `render` extra (`pip install -e "cli[render]"`). The OpenStreetMap data is pinned to one date per sheet, so every contributor renders the same map.

`rutor redraw` checks its own result before it finishes. A blank, too dark, posterised, shifted or unchanged redraw exits with code 5 and one `check:` line per problem. `rutor upload` refuses a cell until the check has passed. `rutor check <sheet> <col>_<row>` runs the check again on the files in `work/`.

## Tests

```bash
python -m unittest discover -s tests -v
```

Without Pillow and numpy the renderer tests are skipped, not passed.
