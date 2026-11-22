"""Orthophoto and OpenStreetMap fetches for one cell.

Orthophoto requests go to the sheet's WMS. OpenStreetMap requests go to
Overpass. Both are cached under the sheet work directory and reused.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import math
import os
import re
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
    # CRS:84 takes longitude first. EPSG:4326 in WMS 1.3.0 takes latitude first.
    crs: str = "CRS:84"


ORTHO_SOURCES: dict[str, OrthoSource] = {
    "goteborg-norr": OrthoSource(
        "https://opengeodata.goteborg.se/services/ortofoto/wms/v1",
        "orto_2025",
    ),
}

# The OpenStreetMap data is pinned per sheet. A query carries the date, so the
# same cell always comes from the same OSM snapshot, and the cache file carries
# it too, so a new date never reads the old answer.
OSM_DATES: dict[str, str] = {
    "goteborg-norr": "2026-11-15T00:00:00Z",
    "stockholm-norr": "2026-11-15T00:00:00Z",
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

# Single trees (node natural=tree) are not in this list: see tree_nodes_query.
TREE_FILTERS = (
    'way["natural"~"tree_row|wood|scrub|grassland"]',
    'way["landuse"~"grass|forest|meadow|village_green|recreation_ground|cemetery"]',
    'way["leisure"~"park|garden|playground|pitch"]',
    'relation["leisure"~"park|garden"]',
    'relation["landuse"~"grass|forest"]',
)


# Which renderer draws a sheet. "open" is the orthophoto and OpenStreetMap renderer in this
# package; "google" is Google's Photorealistic 3D Tiles through Cesium ion, with the
# contributor's own token. A sheet has one kind, so neighbouring cells share a source.
SHEET_KINDS: dict[str, str] = {
    "goteborg-norr": "open",
    "stockholm-norr": "google",
}


def sheet_kind(sheet_id: str) -> str:
    found = SHEET_KINDS.get(sheet_id)
    if found is None:
        raise environment(f"no source decided for sheet {sheet_id} yet")
    return found


def ortho_source(sheet_id: str) -> OrthoSource:
    found = ORTHO_SOURCES.get(sheet_id)
    if found is None:
        raise environment(f"no open orthophoto source for sheet {sheet_id} yet")
    return found


def osm_date(sheet_id: str) -> str:
    found = OSM_DATES.get(sheet_id)
    if found is None:
        raise environment(f"no OpenStreetMap date pinned for sheet {sheet_id}")
    return found


def _date_stamp(date: str) -> str:
    # A colon is not allowed in a Windows file name.
    return date.replace(":", "-")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", text.lower())


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
    if source.crs == "EPSG:4326":
        bbox = f"{south_s},{west_s},{north_s},{east_s}"
    else:
        bbox = f"{west_s},{south_s},{east_s},{north_s}"
    query = urllib.parse.urlencode(
        [
            ("SERVICE", "WMS"),
            ("VERSION", "1.3.0"),
            ("REQUEST", "GetMap"),
            ("LAYERS", source.layer),
            ("STYLES", ""),
            ("CRS", source.crs),
            ("BBOX", bbox),
            ("WIDTH", str(width)),
            ("HEIGHT", str(height)),
            ("FORMAT", "image/png"),
        ]
    )
    joiner = "&" if "?" in source.endpoint else "?"
    return f"{source.endpoint}{joiner}{query}"


def _query(
    filters: tuple[str, ...],
    south: float,
    west: float,
    north: float,
    east: float,
    date: str | None = None,
) -> str:
    west_s, south_s, east_s, north_s = _box_text(west, south, east, north)
    # Overpass bbox order is south, west, north, east.
    box = f"({south_s},{west_s},{north_s},{east_s})"
    header = "[out:json][timeout:90];" if date is None else f'[out:json][timeout:90][date:"{date}"];'
    lines = [header, "("]
    for item in filters:
        lines.append(f"  {item}{box};")
    lines.append(");")
    lines.append("out body; >; out skel qt;")
    return "\n".join(lines) + "\n"


def buildings_query(south: float, west: float, north: float, east: float, date: str | None = None) -> str:
    return _query(BUILDING_FILTERS, south, west, north, east, date)


def trees_query(south: float, west: float, north: float, east: float, date: str | None = None) -> str:
    return _query(TREE_FILTERS, south, west, north, east, date)


def tree_nodes_query(south: float, west: float, north: float, east: float, date: str | None = None) -> str:
    """Single trees, without the date index.

    At a pinned date Overpass looks single trees up in its global node tag history and runs out
    of memory (2 GB) on overpass-api.de and maps.mail.ru alike, even for one cell. Today's trees
    whose current version is older than the pin are the same set, as long as nobody edits or
    deletes them later, and that query is cheap.
    """
    west_s, south_s, east_s, north_s = _box_text(west, south, east, north)
    box = f"({south_s},{west_s},{north_s},{east_s})"
    older = "" if date is None else f'(if: timestamp() < "{date}")'
    return f'[out:json][timeout:90];\nnode["natural"="tree"]{box}{older};\nout body;\n'


def _merge_elements(*payloads: bytes) -> bytes:
    """One Overpass answer from several. A tagged copy of an element wins over a bare one."""
    merged: dict[tuple[str, int], dict[str, object]] = {}
    for payload in payloads:
        parsed = json.loads(payload.decode("utf-8"))
        for element in parsed["elements"]:
            if not isinstance(element, dict):
                continue
            key = (str(element.get("type")), int(element.get("id", 0)))
            known = merged.get(key)
            if known is None or (element.get("tags") and not known.get("tags")):
                merged[key] = element
    return json.dumps({"elements": list(merged.values())}).encode("utf-8")


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
        if isinstance(exc.reason, TimeoutError):
            # Worth another try, so it is reported as a retryable 502.
            return 502, b""
        raise network(f"could not reach {url} ({exc.reason})") from exc
    # A read timeout is a TimeoutError, not a URLError. It, a dropped connection
    # and a broken HTTP stream are all worth another try, so they look like the
    # retryable 502 that fetch_overpass already understands.
    except (TimeoutError, ConnectionError, http.client.HTTPException):
        return 502, b""


def _overpass_problem(payload: bytes) -> str | None:
    """Why an Overpass answer cannot be cached, or None when it can.

    Overpass reports a failed query as HTTP 200 with a `remark`, so the body has
    to be read before it is trusted.
    """
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "the answer was not JSON"
    if not isinstance(parsed, dict):
        return "the answer was not a JSON object"
    if not isinstance(parsed.get("elements"), list):
        return "the answer had no elements list"
    remark = parsed.get("remark")
    if isinstance(remark, str):
        lowered = remark.lower()
        for word in ("error", "timed out", "runtime"):
            if word in lowered:
                return f"the answer said: {remark}"
    return None


def fetch_overpass(query: str, fetch: Fetch | None = None) -> bytes:
    getter = http_request if fetch is None else fetch
    url = os.environ.get("PIXELBYGGET_OVERPASS_URL", OVERPASS_URL).strip() or OVERPASS_URL
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_problem = "no answer"
    for attempt in range(ATTEMPTS):
        status, payload = getter(url, body, OVERPASS_TIMEOUT)
        if status == 200 and payload:
            problem = _overpass_problem(payload)
            if problem is None:
                return payload
            last_problem = problem
        elif status == 200:
            last_problem = "the answer was empty"
        else:
            last_problem = f"HTTP {status}"
        # An unusable body under HTTP 200 is as retryable as a 502.
        retriable = status == 200 or status in RETRIABLE_STATUS
        if not retriable or attempt + 1 == ATTEMPTS:
            raise network(f"Overpass gave up after {attempt + 1} tries: {last_problem}")
        time.sleep(BACKOFF_SECONDS[attempt])
    raise network(f"Overpass gave up after {ATTEMPTS} tries: {last_problem}")


def _grid_steps(camera: dict[str, float]) -> tuple[float, float]:
    lon_px = SHEET_METRES_PER_PIXEL / (METRES_PER_DEGREE * math.cos(math.radians(camera["grid_lat"])))
    lat_px = SHEET_METRES_PER_PIXEL / METRES_PER_DEGREE
    return lon_px, lat_px


def _fetch_tile(sheet_id: str, camera: dict[str, float], tx: int, ty: int, fetch: Fetch) -> bytes:
    """One ORTHO_TILE square of the sheet's photo grid, cached and shared by every cell."""
    path = config.work_dir(sheet_id) / "sources" / "ortho" / _slug(ortho_source(sheet_id).layer) / f"{tx}_{ty}.png"
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


def _usable_osm(path: Path) -> bool:
    """True when the cached file is an OSM answer this module would have written."""
    if not path.is_file():
        return False
    try:
        payload = path.read_bytes()
    except OSError:
        return False
    if _overpass_problem(payload) is None:
        return True
    path.unlink(missing_ok=True)
    return False


def _ortho_key(sheet_id: str, west: float, south: float, east: float, north: float, width: int, height: int) -> str:
    """A short stable name for the exact photo box, so two boxes never share a file."""
    source = ortho_source(sheet_id)
    parts = [
        source.endpoint,
        source.layer,
        f"{west:.{WMS_DECIMALS}f}",
        f"{south:.{WMS_DECIMALS}f}",
        f"{east:.{WMS_DECIMALS}f}",
        f"{north:.{WMS_DECIMALS}f}",
        str(int(width)),
        str(int(height)),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def source_dir(sheet_id: str, col: int, row: int) -> Path:
    return config.work_dir(sheet_id) / "sources" / f"{col}_{row}"


def load_sources(
    sheet_id: str,
    col: int,
    row: int,
    camera: dict[str, float],
    fetch: Fetch | None = None,
) -> tuple[Path, Path, Path]:
    """Return the photo, buildings and trees cache files, fetching what is missing."""
    west = camera["west"]
    south = camera["south"]
    east = camera["east"]
    north = camera["north"]
    # Fail on an unknown sheet or an unpinned date before creating a cache directory.
    ortho_source(sheet_id)
    date = osm_date(sheet_id)
    directory = source_dir(sheet_id, col, row)
    width = int(camera["ortho_width"])
    height = int(camera["ortho_height"])
    orto_path = directory / f"orto_{_ortho_key(sheet_id, west, south, east, north, width, height)}.png"
    osm_path = directory / f"osm_{_date_stamp(date)}.json"
    trees_path = directory / f"trees_{_date_stamp(date)}.json"
    getter = http_request if fetch is None else fetch
    if not orto_path.is_file():
        _store(orto_path, _fetch_ortho(sheet_id, camera, getter))
    if not _usable_osm(osm_path):
        _store(osm_path, fetch_overpass(buildings_query(south, west, north, east, date), fetch=getter))
    if not _usable_osm(trees_path):
        areas = fetch_overpass(trees_query(south, west, north, east, date), fetch=getter)
        single = fetch_overpass(tree_nodes_query(south, west, north, east, date), fetch=getter)
        _store(trees_path, _merge_elements(areas, single))
    return orto_path, osm_path, trees_path
