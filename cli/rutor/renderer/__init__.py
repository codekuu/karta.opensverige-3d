"""Glue between the cell camera, the open-data fetches and the drawing."""

from __future__ import annotations

import json
from pathlib import Path

from rutor.errors import bad_input, environment, network
from rutor.render import camera_for
from rutor.renderer.sources import Fetch, load_sources


def _sheet_id(sheet: dict[str, object]) -> str:
    sheet_id = sheet.get("id")
    if not isinstance(sheet_id, str) or not sheet_id:
        raise bad_input("sheet is missing id")
    return sheet_id


def _load_json(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise network(f"{path.name} is not JSON") from exc
    if not isinstance(raw, dict):
        raise network(f"{path.name} is not a JSON object")
    parsed: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            parsed[key] = value
    return parsed


def render(
    sheet: dict[str, object],
    col: int,
    row: int,
    out: Path,
    fetch: Fetch | None = None,
) -> None:
    """Write the 1536 px source render for one cell. `fetch` replaces HTTP."""
    sheet_id = _sheet_id(sheet)
    camera = camera_for(sheet, col, row)
    orto_path, osm_path, trees_path = load_sources(sheet_id, col, row, camera, fetch=fetch)
    # Pillow is imported here so a machine without the render extra can still
    # import the package, run preflight and fail an unknown sheet.
    try:
        from PIL import Image

        from rutor.renderer.draw import draw
    except ImportError as exc:
        raise environment("render needs pillow and numpy (pip install pillow numpy)") from exc
    try:
        with Image.open(orto_path) as opened:
            orto = opened.convert("RGB")
    except OSError as exc:
        raise network(f"{orto_path.name} is not an image") from exc
    expected = (int(camera["ortho_width"]), int(camera["ortho_height"]))
    if orto.size != expected:
        raise network(f"{orto_path.name} is {orto.size[0]}x{orto.size[1]}, expected {expected[0]}x{expected[1]}")
    image = draw(
        orto,
        _load_json(osm_path),
        _load_json(trees_path),
        camera["center_lat"],
        camera["center_lon"],
        camera["west"],
        camera["south"],
        camera["east"],
        camera["north"],
        camera["lift_m"],
        grid_lat=camera["grid_lat"],
        sheet_x0=int(camera["window_x0"]),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)


__all__ = ["render"]
