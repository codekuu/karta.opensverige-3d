"""The Google source: Photorealistic 3D Tiles through Cesium ion, drawn by headless Chrome.

The contributor's own Cesium ion token is read from CESIUM_ION_TOKEN and handed to node in its
environment, never on a command line or in a file. Google's terms bind that token. The render
stays on the contributor's machine like any source render.

The camera is the one that drew the live map: one orthographic camera per sheet, looking along
the sheet's heading and 45 degrees down, fixed in the earth-centred frame at the sheet's origin.
Every cell moves it sideways in its own picture plane, so neighbours line up to the pixel and a
cell lands on the map pixels it replaces. A point at east e, north n and height h in the plane
that touches the ellipsoid at the origin lands at picture X = e cos a - n sin a,
Y = (e sin a + n cos a + h) / sqrt 2, and map pixel (X - x_meters, y_meters - Y) / 0.25. No
ground height enters: Google's tiles carry their own heights.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from rutor import config
from rutor.errors import RutorError, environment, network
from rutor.frame import ecef as _ecef
from rutor.frame import mix as _mix
from rutor.frame import sheet_basis
from rutor.render import CELL_PX, METRES_PER_PIXEL, WINDOW_PX, _number

ION_ASSET = 2275207
# Must match cli/google-render/package.json; a test holds them together.
CESIUM_VERSION = "1.147.0"
TOKEN_ENV = "CESIUM_ION_TOKEN"
NODE_MAJOR = 22
# Distance and depth the live map's renders used (near 700 m before the target, far 350 m behind).
RANGE_M = 4000.0
NEAR_M = RANGE_M - 700.0
FAR_M = RANGE_M + 350.0
# The live renders' light: east-north-up at the sheet origin, intensity 2.2.
LIGHT_ENU = (-0.45, -0.55, -0.7)
LIGHT_INTENSITY = 2.2
# A render with less spread than this is the black background: no tiles arrived.
MIN_SPREAD = 4.0
NODE_TIMEOUT = 900

Runner = Callable[[list[str], dict[str, str]], tuple[int, str, str]]
Which = Callable[[str], str | None]


def default_render_dir() -> Path:
    return config.repo_root() / "cli" / "google-render"


def google_camera(sheet: dict[str, object], col: int, row: int) -> dict[str, object]:
    """What render.mjs needs to point Cesium at one cell: the live map's camera, moved to the cell."""
    basis = sheet_basis(sheet)
    origin = _ecef(_number(sheet, "origin_lat"), _number(sheet, "origin_lon"), 0.0)
    # The window centre in picture metres: the middle of the cell, as camera_for counts it.
    x = _number(sheet, "x_meters") + (col * CELL_PX + CELL_PX // 2) * METRES_PER_PIXEL
    y = _number(sheet, "y_meters") - (row * CELL_PX + CELL_PX // 2) * METRES_PER_PIXEL
    centre = _mix((origin, 1.0), (basis["right"], x), (basis["forward"], y * math.sqrt(2.0)))
    position = _mix((centre, 1.0), (basis["direction"], -RANGE_M))
    norm = math.sqrt(sum(c * c for c in LIGHT_ENU))
    light = _mix(
        (basis["east"], LIGHT_ENU[0] / norm), (basis["north"], LIGHT_ENU[1] / norm), (basis["up"], LIGHT_ENU[2] / norm)
    )
    half = WINDOW_PX / 2 * METRES_PER_PIXEL
    return {
        "position": list(position),
        "direction": list(basis["direction"]),
        "up": list(basis["camera_up"]),
        "right": list(basis["right"]),
        "frustum": {
            "left": -half,
            "right": half,
            "bottom": -half,
            "top": half,
            "near": NEAR_M,
            "far": FAR_M,
        },
        "light": {"direction": list(light), "intensity": LIGHT_INTENSITY},
        "width": WINDOW_PX,
        "height": WINDOW_PX,
        "ion_asset": ION_ASSET,
    }


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=NODE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise network(f"the Google render took longer than {NODE_TIMEOUT} s") from exc
    except OSError as exc:
        raise environment(f"could not start node: {exc}") from exc
    return done.returncode, done.stdout, done.stderr


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def _node(which: Which, run: Runner, env: dict[str, str]) -> str:
    node = which("node")
    if node is None:
        raise environment(f"the Google source needs Node {NODE_MAJOR} or newer on PATH")
    code, out, _err = run([node, "--version"], env)
    version = out.strip().lstrip("v")
    try:
        major = int(version.split(".")[0])
    except ValueError:
        major = 0
    if code != 0 or major < NODE_MAJOR:
        raise environment(f"the Google source needs Node {NODE_MAJOR} or newer, found {out.strip() or 'nothing'}")
    return node


def _require_installed(root: Path) -> None:
    needed = (
        root / "render.mjs",
        root / "node_modules" / "cesium" / "Build" / "Cesium" / "Cesium.js",
        root / "node_modules" / "puppeteer",
    )
    if not all(path.exists() for path in needed):
        raise environment(f"the Google renderer is not installed: run npm ci in {root}")


def token_missing(env: Mapping[str, str] | None = None) -> str | None:
    environ = os.environ if env is None else env
    if environ.get(TOKEN_ENV, "").strip():
        return None
    return (
        f"{TOKEN_ENV} is not set. The person at this machine makes a free account at "
        f"https://ion.cesium.com, creates a token with the default scopes and sets {TOKEN_ENV} themselves, "
        "where this program can read it. An agent does not make the account and does not handle the token"
    )


def node_missing(which: Which | None = None, run: Runner | None = None, env: Mapping[str, str] | None = None) -> str | None:
    try:
        _node(which or shutil.which, run or _run, dict(os.environ if env is None else env))
    except RutorError as exc:
        return exc.message
    return None


def renderer_missing(root: Path | None = None) -> str | None:
    try:
        _require_installed(root or default_render_dir())
    except RutorError as exc:
        return exc.message
    return None


def not_ready(
    env: Mapping[str, str] | None = None,
    which: Which | None = None,
    run: Runner | None = None,
    render_dir: Path | None = None,
) -> str | None:
    """The first thing this machine lacks for a Google sheet, or None. Asked before a cell is locked."""
    return token_missing(env) or node_missing(which, run, env) or renderer_missing(render_dir)


def _save_rgb(raw: bytes, out: Path) -> None:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise environment("render needs pillow and numpy (pip install pillow numpy)") from exc
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.convert("RGB")
    except OSError as exc:
        raise network("the Google render is not an image") from exc
    if image.size != (WINDOW_PX, WINDOW_PX):
        raise network(f"the Google render is {image.size[0]}x{image.size[1]}, expected {WINDOW_PX}x{WINDOW_PX}")
    if float(np.asarray(image, dtype=np.float32).std()) < MIN_SPREAD:
        raise network("the Google render is blank: no tiles arrived. Check the token and try again later")
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)


def render_google(
    sheet: dict[str, object],
    col: int,
    row: int,
    out: Path,
    run: Runner | None = None,
    env: Mapping[str, str] | None = None,
    which: Which | None = None,
    render_dir: Path | None = None,
) -> dict[str, str]:
    """Write the 1536 px source render for one cell from Google's tiles. Returns what node reported."""
    run = run or _run
    environ = dict(os.environ if env is None else env)
    which = which or shutil.which
    root = render_dir or default_render_dir()
    no_token = token_missing(environ)
    if no_token is not None:
        raise environment(no_token)
    node = _node(which, run, environ)
    _require_installed(root)
    view = google_camera(sheet, col, row)
    out.parent.mkdir(parents=True, exist_ok=True)
    camera_path = out.with_name(out.stem + ".camera.json")
    raw_path = out.with_name(out.stem + ".google.png")
    camera_path.write_text(json.dumps(view, indent=2) + "\n", encoding="utf-8")
    try:
        code, stdout, stderr = run(
            [node, str(root / "render.mjs"), "--camera", str(camera_path), "--out", str(raw_path)], environ
        )
        if code != 0:
            message = f"the Google render failed: {_last_line(stderr)}"
            raise network(message) if code == 4 else environment(message)
        try:
            raw = raw_path.read_bytes()
        except OSError as exc:
            raise network("the Google render wrote no picture") from exc
        _save_rgb(raw, out)
    finally:
        raw_path.unlink(missing_ok=True)
    try:
        reported = json.loads(_last_line(stdout))
    except json.JSONDecodeError:
        reported = {}
    if not isinstance(reported, dict):
        reported = {}
    return {str(key): str(value) for key, value in reported.items()}
