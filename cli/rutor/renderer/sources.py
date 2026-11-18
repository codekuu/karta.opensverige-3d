"""Orthophoto and OpenStreetMap fetches for one cell.

Orthophoto requests go to the sheet's WMS. OpenStreetMap requests go to
Overpass. Both are cached under the sheet work directory and reused.
"""

from __future__ import annotations

import io
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from rutor import config
from rutor.errors import environment, network

USER_AGENT = "pixelbygget-rutor/0.1"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Five tries. The waits sit between tries. The fifth failure stops, so the
# last wait (60 s) is not slept.
BACKOFF_SECONDS = (5, 10, 20, 40, 60)
ATTEMPTS = 5
RETRIABLE_STATUS = frozenset({429, 502, 504})
WMS_TIMEOUT = 60.0
OVERPASS_TIMEOUT = 120.0
# Overpass boxes only need to cover the window. The WMS box must hit the
# sheet's pixel grid, so it carries enough decimals for a millimetre.
OVERPASS_DECIMALS = 5
WMS_DECIMALS = 9
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ORTHO_TILE = 1024
SHEET_METRES_PER_PIXEL = 0.25
METRES_PER_DEGREE = 111320.0

Fetch = Callable[[str, bytes | None, float], tuple[int, bytes]]


class OrthoSource(NamedTuple):
    endpoint: str
    layer: str


ORTHO_SOURCES: dict[str, OrthoSource] = {
    "goteborg-norr": OrthoSource(
        "https://opengeodata.goteborg.se/services/ortofoto/wms/v1",
        "orto_2025",
    ),
}

BUILDING_FILTERS = (
    'way["building"]',
    'way["building:part"]',
    'way["highway"]',
    'way["natural"]',
    'way["landuse"]',
    'way["leisure"]',
    'way["railway"]',
    'way["waterway"]',
    'relation["natural"="water"]',
    'relation["building"]',
)

TREE_FILTERS = (
    'node["natural"="tree"]',
    'way["natural"~"tree_row|wood|scrub|grassland"]',
    'way["landuse"~"grass|forest|meadow|village_green|recreation_ground|cemetery"]',
    'way["leisure"~"park|garden|playground|pitch"]',
    'relation["leisure"~"park|garden"]',
    'relation["landuse"~"grass|forest"]',
)


def ortho_source(sheet_id: str) -> OrthoSource:
    found = ORTHO_SOURCES.get(sheet_id)
    if found is None:
        raise environment(f"no open orthophoto source for sheet {sheet_id} yet")
    return found


def _box_text(west: float, south: float, east: float, north: float, places: int = OVERPASS_DECIMALS) -> tuple[str, str, str, str]:
    return (
        f"{west:.{places}f}",
        f"{south:.{places}f}",
        f"{east:.{places}f}",
        f"{north:.{places}f}",
    )


def wms_url(sheet_id: str, west: float, south: float, east: float, north: float, width: int, height: int) -> str:
    """GetMap for the exact snapped box. PNG, so neighbouring crops get identical pixels."""
    source = ortho_source(sheet_id)
    west_s, south_s, east_s, north_s = _box_text(west, south, east, north, WMS_DECIMALS)
    query = urllib.parse.urlencode(
        [
            ("SERVICE", "WMS"),
            ("VERSION", "1.3.0"),
            ("REQUEST", "GetMap"),
            ("LAYERS", source.layer),
            ("STYLES", ""),
            ("CRS", "CRS:84"),
            ("BBOX", f"{west_s},{south_s},{east_s},{north_s}"),
            ("WIDTH", str(width)),
            ("HEIGHT", str(height)),
            ("FORMAT", "image/png"),
        ]
    )
    return f"{source.endpoint}?{query}"


def _query(filters: tuple[str, ...], south: float, west: float, north: float, east: float) -> str:
    west_s, south_s, east_s, north_s = _box_text(west, south, east, north)
    # Overpass bbox order is south, west, north, east.
    box = f"({south_s},{west_s},{north_s},{east_s})"
    lines = ["[out:json][timeout:90];", "("]
    for item in filters:
        lines.append(f"  {item}{box};")
    lines.append(");")
    lines.append("out body; >; out skel qt;")
    return "\n".join(lines) + "\n"


def buildings_query(south: float, west: float, north: float, east: float) -> str:
    return _query(BUILDING_FILTERS, south, west, north, east)


def trees_query(south: float, west: float, north: float, east: float) -> str:
    return _query(TREE_FILTERS, south, west, north, east)


def build_request(url: str, data: bytes | None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")


def http_request(url: str, data: bytes | None = None, timeout: float = OVERPASS_TIMEOUT) -> tuple[int, bytes]:
    request = build_request(url, data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            return status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read()
        finally:
            exc.close()
    except urllib.error.URLError as exc:
        raise network(f"could not reach {url} ({exc.reason})") from exc


def fetch_overpass(query: str, fetch: Fetch | None = None) -> bytes:
    getter = http_request if fetch is None else fetch
    url = os.environ.get("PIXELBYGGET_OVERPASS_URL", OVERPASS_URL).strip() or OVERPASS_URL
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_status = 0
    for attempt in range(ATTEMPTS):
        status, payload = getter(url, body, OVERPASS_TIMEOUT)
        if status == 200 and payload:
            return payload
        last_status = status
        if status not in RETRIABLE_STATUS or attempt + 1 == ATTEMPTS:
            raise network(f"Overpass gave up after {attempt + 1} tries: HTTP {status}")
        time.sleep(BACKOFF_SECONDS[attempt])
    raise network(f"Overpass gave up after {ATTEMPTS} tries: HTTP {last_status}")


def _grid_steps(camera: dict[str, float]) -> tuple[float, float]:
    lon_px = SHEET_METRES_PER_PIXEL / (METRES_PER_DEGREE * math.cos(math.radians(camera["grid_lat"])))
    lat_px = SHEET_METRES_PER_PIXEL / METRES_PER_DEGREE
    return lon_px, lat_px


def _fetch_tile(sheet_id: str, camera: dict[str, float], tx: int, ty: int, fetch: Fetch) -> bytes:
    """One ORTHO_TILE square of the sheet's photo grid, cached and shared by every cell."""
    path = config.work_dir(sheet_id) / "sources" / "ortho" / f"{tx}_{ty}.png"
    if path.is_file():
        return path.read_bytes()
    lon_px, lat_px = _grid_steps(camera)
    west = camera["grid_lon"] + tx * ORTHO_TILE * lon_px
    north = camera["grid_lat"] - ty * ORTHO_TILE * lat_px
    url = wms_url(
        sheet_id,
        west,
        north - ORTHO_TILE * lat_px,
        west + ORTHO_TILE * lon_px,
        north,
        ORTHO_TILE,
        ORTHO_TILE,
    )
    status, payload = fetch(url, None, WMS_TIMEOUT)
    if status != 200 or not payload:
        raise network(f"orthophoto WMS answered HTTP {status}")
    # A WMS reports errors as XML with HTTP 200, so check the bytes.
    if not payload.startswith(PNG_MAGIC):
        raise network(f"orthophoto WMS did not return a PNG: {payload[:120]!r}")
    _store(path, payload)
    return payload


def _fetch_ortho(sheet_id: str, camera: dict[str, float], fetch: Fetch) -> bytes:
    """The cell's photo, cut from shared sheet tiles.

    The WMS resamples every request on its own, so two overlapping requests do not
    return identical pixels. Cutting every cell from the same tiles does.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise environment("render needs pillow and numpy (pip install pillow numpy)") from exc
    lon_px, lat_px = _grid_steps(camera)
    gx0 = round((camera["west"] - camera["grid_lon"]) / lon_px)
    gy0 = round((camera["grid_lat"] - camera["north"]) / lat_px)
    width = int(camera["ortho_width"])
    height = int(camera["ortho_height"])
    canvas = Image.new("RGB", (width, height))
    for ty in range(gy0 // ORTHO_TILE, (gy0 + height - 1) // ORTHO_TILE + 1):
        for tx in range(gx0 // ORTHO_TILE, (gx0 + width - 1) // ORTHO_TILE + 1):
            payload = _fetch_tile(sheet_id, camera, tx, ty, fetch)
            try:
                with Image.open(io.BytesIO(payload)) as opened:
                    tile = opened.convert("RGB")
            except OSError as exc:
                raise network(f"orthophoto tile {tx}_{ty} is not an image") from exc
            if tile.size != (ORTHO_TILE, ORTHO_TILE):
                raise network(f"orthophoto tile {tx}_{ty} is {tile.size[0]}x{tile.size[1]}, expected {ORTHO_TILE}")
            canvas.paste(tile, (tx * ORTHO_TILE - gx0, ty * ORTHO_TILE - gy0))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _store(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique name per call, so two renders storing the same shared tile cannot collide.
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def source_dir(sheet_id: str, col: int, row: int) -> Path:
    return config.work_dir(sheet_id) / "sources" / f"{col}_{row}"


def load_sources(
    sheet_id: str,
    col: int,
    row: int,
    camera: dict[str, float],
    fetch: Fetch | None = None,
) -> tuple[Path, Path, Path]:
    """Return orto.png, osm.json and trees.json, fetching whatever is not cached."""
    west = camera["west"]
    south = camera["south"]
    east = camera["east"]
    north = camera["north"]
    # Fail on an unknown sheet before creating a cache directory.
    ortho_source(sheet_id)
    directory = source_dir(sheet_id, col, row)
    orto_path = directory / "orto.png"
    osm_path = directory / "osm.json"
    trees_path = directory / "trees.json"
    getter = http_request if fetch is None else fetch
    if not orto_path.is_file():
        _store(orto_path, _fetch_ortho(sheet_id, camera, getter))
    if not osm_path.is_file():
        _store(osm_path, fetch_overpass(buildings_query(south, west, north, east), fetch=getter))
    if not trees_path.is_file():
        _store(trees_path, fetch_overpass(trees_query(south, west, north, east), fetch=getter))
    return orto_path, osm_path, trees_path
