"""Cell camera and the renderer boundary.

The Cesium renderer is ported later into rutor.renderer. Until that package
exists and exports render, render_cell fails with a fixed message.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

from rutor.errors import bad_input, environment

PORT_MESSAGE = (
    "renderer not ported yet: copy the Cesium renderer into "
    "cli/rutor/renderer/ (see docs/superpowers/specs, section 9)"
)

HALF_METRES = 192.0
METRES_PER_PIXEL = 0.25
CELL_PX = 1024
METRES_PER_DEGREE = 111320.0


def _number(sheet: dict[str, object], name: str) -> float:
    value = sheet.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise bad_input(f"sheet is missing {name}")
    return float(value)


def camera_for(sheet: dict[str, object], col: int, row: int) -> dict[str, float]:
    """Ground box of the 1536 px window centred on the 1024 px cell.

    Heading is not stored on the sheet by the service. A missing heading is 0,
    which is the camera the sheet definition uses when it does not say otherwise.
    """
    origin_lat = _number(sheet, "origin_lat")
    origin_lon = _number(sheet, "origin_lon")
    x_metres = _number(sheet, "x_meters")
    y_metres = _number(sheet, "y_meters")
    ground_height = _number(sheet, "ground_height")
    heading_raw = sheet.get("heading", 0)
    if isinstance(heading_raw, bool) or not isinstance(heading_raw, (int, float)):
        heading = 0.0
    else:
        heading = float(heading_raw)
    px = col * CELL_PX + CELL_PX // 2
    py = row * CELL_PX + CELL_PX // 2
    x = x_metres + px * METRES_PER_PIXEL
    y = y_metres - py * METRES_PER_PIXEL
    return {
        "center_lat": origin_lat + y / METRES_PER_DEGREE,
        "center_lon": origin_lon + x / (METRES_PER_DEGREE * math.cos(math.radians(origin_lat))),
        "half_width_m": HALF_METRES,
        "half_height_m": HALF_METRES,
        "heading": heading,
        "ground_height": ground_height,
    }


def _ported_render() -> Callable[..., None] | None:
    init = Path(__file__).resolve().parent / "renderer" / "__init__.py"
    if not init.is_file():
        return None
    from rutor import renderer

    render = getattr(renderer, "render", None)
    if not callable(render):
        return None
    return render


def render_cell(sheet: dict[str, object], col: int, row: int, key: str, out: Path) -> None:
    render = _ported_render()
    if render is None:
        raise environment(PORT_MESSAGE)
    render(sheet, col, row, key, out)
