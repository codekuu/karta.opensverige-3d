"""Cell camera and the call into the open-data renderer."""

from __future__ import annotations

import math
from pathlib import Path

from rutor.errors import bad_input
from rutor.frame import SheetFrame, sheet_number

METRES_PER_PIXEL = 0.25
CELL_PX = 1024
WINDOW_PX = 1536
METRES_PER_DEGREE = 111320.0
HALF_EW_M = 260.0
HALF_NS_M = 420.0
ORTHO_WIDTH = int(2 * HALF_EW_M / METRES_PER_PIXEL)
ORTHO_HEIGHT = int(2 * HALF_NS_M / METRES_PER_PIXEL)


def _number(sheet: dict[str, object], name: str) -> float:
    return sheet_number(sheet, name)


def camera_for(
    sheet: dict[str, object],
    col: int,
    row: int,
    ground_ellipsoid_m: float | None = None,
) -> dict[str, float]:
    """The ground under the 1536 px window, and the photo box around it.

    The centre is the point on the ground that the live map's camera put on the middle of the
    cell (rutor/frame.py), at ground_ellipsoid_m above the ellipsoid. Without it the sheet's
    ground_height is used. Higher ground lands higher in the picture, so its centre lies
    further south. The photo box is snapped to the sheet's 0.25 m photo grid, counted from the
    origin with one longitude scale per sheet, so neighbouring cells sample identical pixels.
    """
    origin_lat = _number(sheet, "origin_lat")
    origin_lon = _number(sheet, "origin_lon")
    ground_height = _number(sheet, "ground_height")
    ground = ground_height if ground_ellipsoid_m is None else float(ground_ellipsoid_m)
    frame = SheetFrame.from_sheet(sheet)
    window_x0 = col * CELL_PX - (WINDOW_PX - CELL_PX) // 2
    window_y0 = row * CELL_PX - (WINDOW_PX - CELL_PX) // 2
    center_lat, center_lon = frame.ground_point(col * CELL_PX + CELL_PX / 2, row * CELL_PX + CELL_PX / 2, ground)
    lon_scale = METRES_PER_DEGREE * math.cos(math.radians(origin_lat))
    lon_px = METRES_PER_PIXEL / lon_scale
    lat_px = METRES_PER_PIXEL / METRES_PER_DEGREE
    photo_gx0 = round((center_lon - HALF_EW_M / lon_scale - origin_lon) / lon_px)
    photo_gy0 = round((origin_lat - (center_lat + HALF_NS_M / METRES_PER_DEGREE)) / lat_px)
    west = origin_lon + photo_gx0 * lon_px
    north = origin_lat - photo_gy0 * lat_px
    return {
        "center_lat": center_lat,
        "center_lon": center_lon,
        "half_ew_m": HALF_EW_M,
        "half_ns_m": HALF_NS_M,
        "west": west,
        "south": north - ORTHO_HEIGHT * lat_px,
        "east": west + ORTHO_WIDTH * lon_px,
        "north": north,
        "ortho_width": ORTHO_WIDTH,
        "ortho_height": ORTHO_HEIGHT,
        "grid_lat": origin_lat,
        "grid_lon": origin_lon,
        "photo_gx0": photo_gx0,
        "photo_gy0": photo_gy0,
        "window_x0": window_x0,
        "window_y0": window_y0,
        "heading": frame.heading,
        "ground_height": ground_height,
        "ground_ellipsoid_m": ground,
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
