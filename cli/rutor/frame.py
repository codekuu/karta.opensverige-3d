"""The sheet's picture frame: where a point on the earth lands on the live map.

The live map was drawn by one orthographic camera per sheet, fixed in the earth-centred frame at
the sheet origin (height 0 on the WGS84 ellipsoid), looking along the sheet's heading and 45
degrees down. A point lands at picture metres X = (P - O) . right and Y = (P - O) . camera_up,
where P and O are earth-centred, and on map pixel ((X - x_meters) / 0.25, (y_meters - Y) / 0.25).
The site's own projection (geometri.ts) and the renderer that drew the map (grid-config.ts) use
this frame, so the Google camera and the open renderer use it too. Curvature is in it: 20 km from
the origin the ground lies about 31 m below the origin's tangent plane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rutor.errors import bad_input

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)
METRES_PER_PIXEL = 0.25

Vec = tuple[float, float, float]


def sheet_number(sheet: dict[str, object], name: str) -> float:
    value = sheet.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise bad_input(f"sheet is missing {name}")
    return float(value)


def sheet_heading(sheet: dict[str, object]) -> float:
    """The heading in degrees. A missing or malformed heading is 0, as the service leaves it out."""
    raw = sheet.get("heading", 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    return float(raw)


def ecef(lat_deg: float, lon_deg: float, height: float) -> Vec:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
    return (
        (n + height) * math.cos(lat) * math.cos(lon),
        (n + height) * math.cos(lat) * math.sin(lon),
        (n * (1 - WGS84_E2) + height) * math.sin(lat),
    )


def mix(*parts: tuple[Vec, float]) -> Vec:
    return (
        sum(v[0] * k for v, k in parts),
        sum(v[1] * k for v, k in parts),
        sum(v[2] * k for v, k in parts),
    )


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sheet_basis(sheet: dict[str, object]) -> dict[str, Vec]:
    """East, north and up at the sheet origin, and the camera's axes in the earth-centred frame."""
    lat = math.radians(sheet_number(sheet, "origin_lat"))
    lon = math.radians(sheet_number(sheet, "origin_lon"))
    heading = math.radians(sheet_heading(sheet))
    east: Vec = (-math.sin(lon), math.cos(lon), 0.0)
    north: Vec = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat))
    up: Vec = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
    r = math.sqrt(0.5)
    forward = mix((east, math.sin(heading)), (north, math.cos(heading)))
    return {
        "east": east,
        "north": north,
        "up": up,
        "forward": forward,
        "direction": mix((forward, r), (up, -r)),
        "camera_up": mix((forward, r), (up, r)),
        "right": mix((east, math.cos(heading)), (north, -math.sin(heading))),
    }


@dataclass(frozen=True)
class SheetFrame:
    origin: Vec
    right: Vec
    camera_up: Vec
    origin_lat: float
    origin_lon: float
    heading: float
    x_meters: float
    y_meters: float

    @staticmethod
    def from_sheet(sheet: dict[str, object]) -> SheetFrame:
        basis = sheet_basis(sheet)
        origin_lat = sheet_number(sheet, "origin_lat")
        origin_lon = sheet_number(sheet, "origin_lon")
        return SheetFrame(
            origin=ecef(origin_lat, origin_lon, 0.0),
            right=basis["right"],
            camera_up=basis["camera_up"],
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            heading=sheet_heading(sheet),
            x_meters=sheet_number(sheet, "x_meters"),
            y_meters=sheet_number(sheet, "y_meters"),
        )

    def picture(self, lat: float, lon: float, height: float) -> tuple[float, float]:
        """Picture metres of a point at a height above the ellipsoid."""
        p = ecef(lat, lon, height)
        d: Vec = (p[0] - self.origin[0], p[1] - self.origin[1], p[2] - self.origin[2])
        return _dot(d, self.right), _dot(d, self.camera_up)

    def map_pixel(self, lat: float, lon: float, height: float) -> tuple[float, float]:
        x, y = self.picture(lat, lon, height)
        return (x - self.x_meters) / METRES_PER_PIXEL, (self.y_meters - y) / METRES_PER_PIXEL

    def up_factor(self, lat: float, lon: float) -> float:
        """Picture metres up per metre of height at a point: the local up dotted with the camera's up."""
        la = math.radians(lat)
        lo = math.radians(lon)
        up: Vec = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
        return _dot(up, self.camera_up)

    def ground_point(self, px: float, py: float, height: float) -> tuple[float, float]:
        """Latitude and longitude of the point at this height that lands on map pixel (px, py)."""
        x = self.x_meters + px * METRES_PER_PIXEL
        y = self.y_meters - py * METRES_PER_PIXEL
        a = math.radians(self.heading)
        forward = y * math.sqrt(2.0) - height
        east = x * math.cos(a) + forward * math.sin(a)
        north = -x * math.sin(a) + forward * math.cos(a)
        lat = self.origin_lat + north / 111320.0
        lon = self.origin_lon + east / (111320.0 * math.cos(math.radians(self.origin_lat)))
        step = 1e-7
        for _ in range(20):
            fx, fy = self.map_pixel(lat, lon, height)
            rx, ry = px - fx, py - fy
            if abs(rx) < 1e-9 and abs(ry) < 1e-9:
                break
            lat_x, lat_y = self.map_pixel(lat + step, lon, height)
            lon_x, lon_y = self.map_pixel(lat, lon + step, height)
            a11, a21 = (lat_x - fx) / step, (lat_y - fy) / step
            a12, a22 = (lon_x - fx) / step, (lon_y - fy) / step
            det = a11 * a22 - a12 * a21
            lat += (rx * a22 - a12 * ry) / det
            lon += (a11 * ry - a21 * rx) / det
        return lat, lon
