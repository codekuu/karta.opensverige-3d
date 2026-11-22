"""Cell camera and the call into the open-data renderer."""

from __future__ import annotations

import math
from pathlib import Path

from rutor.errors import bad_input

METRES_PER_PIXEL = 0.25
CELL_PX = 1024
WINDOW_PX = 1536
METRES_PER_DEGREE = 111320.0
HALF_EW_M = 260.0
HALF_NS_M = 420.0
LIFT_M = 34.7
ORTHO_WIDTH = int(2 * HALF_EW_M / METRES_PER_PIXEL)
ORTHO_HEIGHT = int(2 * HALF_NS_M / METRES_PER_PIXEL)


def _number(sheet: dict[str, object], name: str) -> float:
    value = sheet.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise bad_input(f"sheet is missing {name}")
    return float(value)


def camera_for(sheet: dict[str, object], col: int, row: int) -> dict[str, float]:
    """Centre and fetch box of the 1536 px window on the 1024 px cell.

    The sheet is a 45-degree oblique view, so ground y is the screen y divided
    by cos 45. Heading is not stored on the sheet by the service. A missing
    heading is 0, which is the camera the sheet definition uses when it does
    not say otherwise. lift_m is the fixed height offset measured for the live
    sheets.
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
    x = x_metres + (col * CELL_PX + CELL_PX // 2) * METRES_PER_PIXEL
    y_ground = (y_metres - (row * CELL_PX + CELL_PX // 2) * METRES_PER_PIXEL) / math.cos(math.radians(45.0))
    center_lat = origin_lat + y_ground / METRES_PER_DEGREE
    lon_scale = METRES_PER_DEGREE * math.cos(math.radians(origin_lat))
    center_lon = origin_lon + x / lon_scale
    # Snap the fetch box to the sheet's own 0.25 m pixel grid, measured from the
    # origin, so neighbouring cells sample the photo on identical pixels.
    lon_px = METRES_PER_PIXEL / lon_scale
    lat_px = METRES_PER_PIXEL / METRES_PER_DEGREE
    west = origin_lon + round((center_lon - HALF_EW_M / lon_scale - origin_lon) / lon_px) * lon_px
    south = origin_lat + round((center_lat - HALF_NS_M / METRES_PER_DEGREE - origin_lat) / lat_px) * lat_px
    return {
        "center_lat": center_lat,
        "center_lon": center_lon,
        "half_ew_m": HALF_EW_M,
        "half_ns_m": HALF_NS_M,
        "west": west,
        "south": south,
        "east": west + ORTHO_WIDTH * lon_px,
        "north": south + ORTHO_HEIGHT * lat_px,
        "ortho_width": ORTHO_WIDTH,
        "ortho_height": ORTHO_HEIGHT,
        "grid_lat": origin_lat,
        "grid_lon": origin_lon,
        "window_x0": col * CELL_PX - (WINDOW_PX - CELL_PX) // 2,
        "heading": heading,
        "ground_height": ground_height,
        "lift_m": LIFT_M,
    }


def render_cell(sheet: dict[str, object], col: int, row: int, out: Path) -> None:
    from rutor import renderer
    from rutor.renderer import google
    from rutor.renderer.sources import sheet_kind

    sheet_id = sheet.get("id")
    if not isinstance(sheet_id, str) or not sheet_id:
        raise bad_input("sheet is missing id")
    if sheet_kind(sheet_id) == "google":
        google.render_google(sheet, col, row, out)
    else:
        renderer.render(sheet, col, row, out)
