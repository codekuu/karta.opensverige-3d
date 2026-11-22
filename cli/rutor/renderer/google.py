"""The Google source: Photorealistic 3D Tiles through Cesium ion, drawn by headless Chrome.

The contributor's own Cesium ion token is read from CESIUM_ION_TOKEN and handed to node in its
environment, never on a command line or in a file. Google's terms bind that token. The render
stays on the contributor's machine like any source render.

The camera is the open renderer's camera: the same cell centre, heading 0, pitch -45, an
orthographic 1536 px window at 0.25 m per pixel. The target sits lift_m below the sheet's
ground_height, which is where the open renderer puts the ground on the screen.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from rutor import config
from rutor.errors import environment, network
from rutor.render import METRES_PER_PIXEL, WINDOW_PX, camera_for

ION_ASSET = 2275207
# Must match cli/google-render/package.json; a test holds them together.
CESIUM_VERSION = "1.147.0"
TOKEN_ENV = "CESIUM_ION_TOKEN"
NODE_MAJOR = 22
RANGE_M = 3000.0
DEPTH_M = 2000.0
# A render with less spread than this is the black background: no tiles arrived.
MIN_SPREAD = 4.0
NODE_TIMEOUT = 900

Runner = Callable[[list[str], dict[str, str]], tuple[int, str, str]]
Which = Callable[[str], str | None]


def default_render_dir() -> Path:
    return config.repo_root() / "cli" / "google-render"


def google_camera(camera: dict[str, float]) -> dict[str, object]:
    """What render.mjs needs to point Cesium at one cell."""
    half = WINDOW_PX / 2 * METRES_PER_PIXEL
    return {
        "lat": camera["center_lat"],
        "lon": camera["center_lon"],
        "height_m": camera["ground_height"] - camera["lift_m"],
        "heading_deg": float(camera["heading"]),
        "pitch_deg": -45.0,
        "range_m": RANGE_M,
        "frustum": {
            "left": -half,
            "right": half,
            "bottom": -half,
            "top": half,
            "near": RANGE_M - DEPTH_M,
            "far": RANGE_M + DEPTH_M,
        },
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
    if not environ.get(TOKEN_ENV, "").strip():
        raise environment(
            f"{TOKEN_ENV} is not set. Make a free account at https://ion.cesium.com, create a token "
            f"with the default scopes, and set {TOKEN_ENV} in this terminal"
        )
    node = _node(which, run, environ)
    _require_installed(root)
    view = google_camera(camera_for(sheet, col, row))
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
