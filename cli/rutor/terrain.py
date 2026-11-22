"""Ground heights for the open renderer, from a city's open terrain model.

Göteborgs Stad publishes Höjdmodell 2022 (CC0): ground heights in RH 2000 from airborne laser
scanning, 0.5 m pixels, one 1 km GeoTIFF per square of SWEREF 99 12 00, packed in one zip per
10 km square. The zips are hundreds of megabytes, so a tile is read out of its zip with HTTP
range requests: the zip's directory, then the one member. Each tile is cached under the sheet's
work directory and shared by every cell.

A height above the ellipsoid, which the live map's camera works in, is the RH 2000 height plus
the geoid height. Over one sheet the geoid moves less than a metre, so a sheet has one value.
"""

from __future__ import annotations

import io
import math
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from rutor import config
from rutor.errors import environment, network

USER_AGENT = "pixelbygget-rutor/0.1"
RANGE_TIMEOUT = 60.0
RANGE_ATTEMPTS = 3
RANGE_BACKOFF_SECONDS = (5, 15)
GRS80_A = 6378137.0
GRS80_F = 1 / 298.257222101

# (status, total size of the file, the bytes asked for). 404 means the file is not there.
RangedFetch = Callable[[str, int, int], tuple[int, int, bytes]]


class TerrainSource(NamedTuple):
    # A zip per 10 km square; {zip} is like 640_14 (northing and easting in 10 km).
    archive_url: str
    # The projection the tiles are in: a transverse Mercator on SWEREF 99.
    lon0: float
    k0: float
    false_easting: float
    # Geoid height over the sheet, metres: ellipsoid height = RH 2000 height + this.
    geoid_m: float
    # Named in the source manifest, so a render placed on real ground can be told from an old one.
    label: str
    tile_m: int = 1000
    pixel_m: float = 0.5


TERRAIN_SOURCES: dict[str, TerrainSource] = {
    # SWEN17_RH2000 is 36.49 m at Lantmäteriet's control point 17 (57.14 N, 12.33 E), 36.92 m at
    # point 1 (59.06 N, 11.22 E) and 32.79 m at point 18 (57.70 N, 14.30 E). A plane through the
    # three gives 36.7 m at the origin, within about 0.5 m over the sheet.
    "goteborg-norr": TerrainSource(
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_{zip}.zip",
        12.0,
        1.0,
        150000.0,
        36.7,
        "hojdmodell_2022",
    ),
}


def terrain_label(sheet_id: str) -> str:
    """The terrain model a sheet is drawn on, for the source manifest: flat when it has none."""
    found = TERRAIN_SOURCES.get(sheet_id)
    return "flat" if found is None else found.label


def terrain_source(sheet_id: str) -> TerrainSource:
    found = TERRAIN_SOURCES.get(sheet_id)
    if found is None:
        raise environment(f"no open terrain model for sheet {sheet_id} yet")
    return found


def sweref99_tm(lat: float, lon: float, lon0: float, k0: float, false_easting: float) -> tuple[float, float]:
    """Northing and easting on a SWEREF 99 transverse Mercator (Krüger's formulas, Lantmäteriet)."""
    e2 = GRS80_F * (2 - GRS80_F)
    n = GRS80_F / (2 - GRS80_F)
    a_hat = GRS80_A / (1 + n) * (1 + n * n / 4 + n**4 / 64)
    big_a = e2
    big_b = (5 * e2**2 - e2**3) / 6
    big_c = (104 * e2**3 - 45 * e2**4) / 120
    big_d = (1237 * e2**4) / 1260
    phi = math.radians(lat)
    dlam = math.radians(lon - lon0)
    s = math.sin(phi)
    phi_star = phi - s * math.cos(phi) * (big_a + big_b * s * s + big_c * s**4 + big_d * s**6)
    xi = math.atan(math.tan(phi_star) / math.cos(dlam))
    eta = math.atanh(math.cos(phi_star) * math.sin(dlam))
    b1 = n / 2 - 2 * n * n / 3 + 5 * n**3 / 16 + 41 * n**4 / 180
    b2 = 13 * n * n / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440
    b3 = 61 * n**3 / 240 - 103 * n**4 / 140
    b4 = 49561 * n**4 / 161280
    northing = (
        k0
        * a_hat
        * (
            xi
            + b1 * math.sin(2 * xi) * math.cosh(2 * eta)
            + b2 * math.sin(4 * xi) * math.cosh(4 * eta)
            + b3 * math.sin(6 * xi) * math.cosh(6 * eta)
            + b4 * math.sin(8 * xi) * math.cosh(8 * eta)
        )
    )
    easting = (
        k0
        * a_hat
        * (
            eta
            + b1 * math.cos(2 * xi) * math.sinh(2 * eta)
            + b2 * math.cos(4 * xi) * math.sinh(4 * eta)
            + b3 * math.cos(6 * xi) * math.sinh(6 * eta)
            + b4 * math.cos(8 * xi) * math.sinh(8 * eta)
        )
        + false_easting
    )
    return northing, easting


def ranged_http(url: str, start: int, end: int) -> tuple[int, int, bytes]:
    """Bytes start to end (inclusive) of a file over HTTP, with the file's total size."""
    last = "no answer"
    for attempt in range(RANGE_ATTEMPTS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"})
        try:
            with urllib.request.urlopen(request, timeout=RANGE_TIMEOUT) as response:
                status = int(getattr(response, "status", 200))
                body = response.read()
                if status == 206:
                    total = int(str(response.headers.get("Content-Range", "/0")).rsplit("/", 1)[-1])
                    return 206, total, body
                # A server that ignores Range sends the whole file.
                return status, len(body), body[start : end + 1]
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 404:
                return 404, 0, b""
            last = f"HTTP {exc.code}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = str(getattr(exc, "reason", exc))
        if attempt + 1 < RANGE_ATTEMPTS:
            time.sleep(RANGE_BACKOFF_SECONDS[attempt])
    raise network(f"the terrain model did not answer: {last} ({url})")


class _RangeReader(io.RawIOBase):
    """A seekable file over range requests, so zipfile reads only the directory and one member."""

    def __init__(self, url: str, fetch: RangedFetch) -> None:
        self.url = url
        self.fetch = fetch
        self.pos = 0
        status, total, _head = fetch(url, 0, 0)
        self.found = status != 404
        self.size = total

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        if self.pos >= self.size or len(buffer) == 0:
            return 0
        end = min(self.size, self.pos + len(buffer)) - 1
        status, _total, data = self.fetch(self.url, self.pos, end)
        if status not in (200, 206) or not data:
            raise network(f"the terrain model answered HTTP {status} for a part of {self.url}")
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)


def _store(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _tile_names(key: tuple[int, int]) -> tuple[str, str]:
    north_km, east_km = key
    archive = f"{north_km // 10}_{east_km // 10}"
    return archive, f"{archive}/{north_km}_{east_km}.tif"


def _fetch_tile(sheet_id: str, source: TerrainSource, key: tuple[int, int], fetch: RangedFetch) -> bytes | None:
    directory = config.work_dir(sheet_id) / "sources" / "terrain"
    path = directory / f"{key[0]}_{key[1]}.tif"
    missing = directory / f"{key[0]}_{key[1]}.none"
    if path.is_file():
        return path.read_bytes()
    if missing.is_file():
        return None
    archive, member = _tile_names(key)
    reader = _RangeReader(source.archive_url.format(zip=archive), fetch)
    payload: bytes | None = None
    if reader.found:
        try:
            with zipfile.ZipFile(io.BufferedReader(reader, buffer_size=1 << 16)) as opened:
                if member in opened.namelist():
                    payload = opened.read(member)
        except zipfile.BadZipFile as exc:
            raise network(f"the terrain archive {archive} is not a zip") from exc
    if payload is None:
        _store(missing, b"")
        return None
    _store(path, payload)
    return payload


class Terrain:
    """Ground heights from cached tiles. Nodata and tiles the city does not have read as sea level."""

    def __init__(self, source: TerrainSource, tiles: dict[tuple[int, int], object]) -> None:
        self.source = source
        self.tiles = tiles
        self.per_tile = int(round(source.tile_m / source.pixel_m))

    def _value(self, col: int, north_index: int) -> float:
        # col counts pixels east from easting 0, north_index counts pixels north from northing 0.
        key = (north_index // self.per_tile, col // self.per_tile)
        if key not in self.tiles:
            # A lookup outside the loaded box would read as sea level in one cell and as ground in
            # its neighbour, so it is an error, never a guess.
            raise network(f"terrain tile {key[0]}_{key[1]} was not loaded for this cell")
        tile = self.tiles[key]
        if tile is None:
            return 0.0
        row = self.per_tile - 1 - (north_index - key[0] * self.per_tile)
        value = float(tile[row, col - key[1] * self.per_tile])  # type: ignore[index]
        return value

    def height_rh2000(self, northing: float, easting: float) -> float:
        """Bilinear between pixel centres."""
        pixel = self.source.pixel_m
        fc = easting / pixel - 0.5
        fk = northing / pixel - 0.5
        c0 = math.floor(fc)
        k0 = math.floor(fk)
        tx = fc - c0
        ty = fk - k0
        return (
            self._value(c0, k0) * (1 - tx) * (1 - ty)
            + self._value(c0 + 1, k0) * tx * (1 - ty)
            + self._value(c0, k0 + 1) * (1 - tx) * ty
            + self._value(c0 + 1, k0 + 1) * tx * ty
        )

    def ellipsoid(self, northing: float, easting: float) -> float:
        return self.height_rh2000(northing, easting) + self.source.geoid_m

    def ellipsoid_at(self, lat: float, lon: float) -> float:
        northing, easting = sweref99_tm(lat, lon, self.source.lon0, self.source.k0, self.source.false_easting)
        return self.ellipsoid(northing, easting)

    def projected(self, lat: float, lon: float) -> tuple[float, float]:
        return sweref99_tm(lat, lon, self.source.lon0, self.source.k0, self.source.false_easting)


def projected_box(source: TerrainSource, camera: dict[str, float], margin_m: float) -> tuple[float, float, float, float]:
    """The photo box in the terrain model's projection, grown by margin_m on every side."""
    points: list[tuple[float, float]] = []
    for lat in (camera["south"], (camera["south"] + camera["north"]) / 2, camera["north"]):
        for lon in (camera["west"], (camera["west"] + camera["east"]) / 2, camera["east"]):
            points.append(sweref99_tm(lat, lon, source.lon0, source.k0, source.false_easting))
    return (
        min(e for _n, e in points) - margin_m,
        min(n for n, _e in points) - margin_m,
        max(e for _n, e in points) + margin_m,
        max(n for n, _e in points) + margin_m,
    )


def load_terrain(
    sheet_id: str,
    box: tuple[float, float, float, float],
    ranged: RangedFetch | None = None,
) -> Terrain:
    """Tiles covering box (west easting, south northing, east easting, north northing), fetched once."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise environment("render needs pillow and numpy (pip install pillow numpy)") from exc
    source = terrain_source(sheet_id)
    fetch = ranged_http if ranged is None else ranged
    west, south, east, north = box
    # One pixel of margin, so a bilinear lookup at the box edge has its neighbours.
    margin = source.pixel_m
    tiles: dict[tuple[int, int], object] = {}
    for north_km in range(math.floor((south - margin) / source.tile_m), math.floor((north + margin) / source.tile_m) + 1):
        for east_km in range(math.floor((west - margin) / source.tile_m), math.floor((east + margin) / source.tile_m) + 1):
            key = (north_km, east_km)
            payload = _fetch_tile(sheet_id, source, key, fetch)
            if payload is None:
                tiles[key] = None
                continue
            try:
                with Image.open(io.BytesIO(payload)) as opened:
                    values = np.asarray(opened, dtype=np.float32).copy()
            except OSError as exc:
                raise network(f"terrain tile {north_km}_{east_km} is not a readable GeoTIFF") from exc
            per_tile = int(round(source.tile_m / source.pixel_m))
            if values.shape != (per_tile, per_tile):
                raise network(f"terrain tile {north_km}_{east_km} is {values.shape[1]}x{values.shape[0]}, expected {per_tile}")
            # Nodata is -9999; anything far below sea level is not ground.
            values[values < -100.0] = 0.0
            tiles[key] = values
    return Terrain(source, tiles)
