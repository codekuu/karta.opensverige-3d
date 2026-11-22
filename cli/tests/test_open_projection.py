"""The open renderer's projection, terrain and cell centre against numbers from outside this repo."""

from __future__ import annotations

import io
import math
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from rutor.errors import RutorError
from rutor.frame import SheetFrame
from rutor.terrain import sweref99_tm


def _drawing_available() -> bool:
    try:
        import numpy
        from PIL import Image
    except ImportError:
        return False
    del numpy, Image
    return True


DRAWING = _drawing_available()


def _sheet() -> dict[str, object]:
    return {
        "id": "goteborg-norr",
        "name": "Göteborg",
        "cols": 130,
        "rows": 118,
        "origin_lat": 57.707,
        "origin_lon": 11.967,
        "x_meters": -16384,
        "y_meters": 13312,
        "ground_height": 40,
        "heading": 0,
    }


# Picture metres from the renderer that drew the live map (grid-config.ts, SIMCITY_GRID=
# goteborg-grid-north): the point's earth-centred offset from the origin dotted with the camera's
# right and up axes. Heights are above the ellipsoid.
LIVE_PICTURE = (
    (57.707, 11.967, 0.0, 0.0, 0.0),
    (57.707, 11.967, 36.7, -8.525979922069382e-11, 25.950818870027955),
    (57.70607, 11.9714, 46.7, 262.31505852163076, -40.21659601855821),
    (57.93, 11.71, 40.0, -15226.835242733812, 17563.74317489934),
    (57.55, 12.24, 140.0, 16345.582626718986, -12273.580503304398),
    (57.8, 12.1, 146.7, 7908.64720397337, 7423.98412375262),
)


class FrameTests(unittest.TestCase):
    def test_points_land_where_the_live_renderer_put_them(self) -> None:
        frame = SheetFrame.from_sheet(_sheet())
        for lat, lon, height, x, y in LIVE_PICTURE:
            found_x, found_y = frame.picture(lat, lon, height)
            self.assertAlmostEqual(found_x, x, delta=1e-6, msg=f"X at {lat}, {lon}, {height}")
            self.assertAlmostEqual(found_y, y, delta=1e-6, msg=f"Y at {lat}, {lon}, {height}")

    def test_map_pixel_counts_from_the_sheet_corner(self) -> None:
        frame = SheetFrame.from_sheet(_sheet())
        lat, lon, height, x, y = LIVE_PICTURE[3]
        px, py = frame.map_pixel(lat, lon, height)
        self.assertAlmostEqual(px, (x + 16384) / 0.25, delta=1e-5)
        self.assertAlmostEqual(py, (13312 - y) / 0.25, delta=1e-5)

    def test_ground_point_is_the_inverse_of_map_pixel(self) -> None:
        frame = SheetFrame.from_sheet(_sheet())
        for px, py, height in ((66048.0, 53760.0, 80.0), (512.0, 512.0, 36.7), (132608.0, 120320.0, 0.0)):
            lat, lon = frame.ground_point(px, py, height)
            back = frame.map_pixel(lat, lon, height)
            self.assertAlmostEqual(back[0], px, delta=1e-6)
            self.assertAlmostEqual(back[1], py, delta=1e-6)

    def test_one_metre_up_is_cos_45_in_the_picture(self) -> None:
        frame = SheetFrame.from_sheet(_sheet())
        low = frame.picture(57.75, 12.0, 40.0)
        high = frame.picture(57.75, 12.0, 41.0)
        # 5 km from the origin the local up has tilted by 0.04 degrees.
        self.assertAlmostEqual(high[1] - low[1], math.sqrt(0.5), delta=2e-3)
        self.assertAlmostEqual(frame.up_factor(57.75, 12.0), high[1] - low[1], delta=1e-9)


# Lantmäteriet's STAC (stac-hojd, collection dtm-cog) gives tile 640_32 as the SWEREF 99 TM square
# east 320 000 to 330 000 and north 6 400 000 to 6 410 000, and this WGS84 box around it.
TILE_640_32_BBOX = (11.971400764461414, 57.70607237387218, 12.146493004922368, 57.79967571342694)


def _invert_tm(northing: float, easting: float) -> tuple[float, float]:
    lat, lon = 57.75, 12.06
    for _ in range(30):
        n0, e0 = sweref99_tm(lat, lon, 15.0, 0.9996, 500000.0)
        dn_lat = (sweref99_tm(lat + 1e-6, lon, 15.0, 0.9996, 500000.0)[0] - n0) / 1e-6
        de_lat = (sweref99_tm(lat + 1e-6, lon, 15.0, 0.9996, 500000.0)[1] - e0) / 1e-6
        dn_lon = (sweref99_tm(lat, lon + 1e-6, 15.0, 0.9996, 500000.0)[0] - n0) / 1e-6
        de_lon = (sweref99_tm(lat, lon + 1e-6, 15.0, 0.9996, 500000.0)[1] - e0) / 1e-6
        det = dn_lat * de_lon - dn_lon * de_lat
        r_n, r_e = northing - n0, easting - e0
        lat += (r_n * de_lon - dn_lon * r_e) / det
        lon += (dn_lat * r_e - de_lat * r_n) / det
    return lat, lon


class SwerefTests(unittest.TestCase):
    def test_sweref99_tm_matches_lantmateriets_tile_box(self) -> None:
        corners = [_invert_tm(n, e) for n in (6400000.0, 6410000.0) for e in (320000.0, 330000.0)]
        west = min(lon for _lat, lon in corners)
        east = max(lon for _lat, lon in corners)
        south = min(lat for lat, _lon in corners)
        north = max(lat for lat, _lon in corners)
        for found, wanted in zip((west, south, east, north), TILE_640_32_BBOX):
            self.assertAlmostEqual(found, wanted, delta=1e-7)

    def test_the_central_meridian_maps_to_the_false_easting(self) -> None:
        northing, easting = sweref99_tm(57.7, 12.0, 12.0, 1.0, 150000.0)
        self.assertAlmostEqual(easting, 150000.0, delta=1e-6)
        self.assertGreater(northing, 6390000.0)
        self.assertLess(northing, 6410000.0)


def _tile_tif(values: object) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(values, mode="F").save(buffer, format="TIFF", compression="tiff_lzw")
    return buffer.getvalue()


def _city_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
class TerrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("PIXELBYGGET_HOME")
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        self._root_patch = patch("rutor.config.repo_root", return_value=Path(self.tmp.name))
        self._root_patch.start()

    def tearDown(self) -> None:
        self._root_patch.stop()
        if self._saved is None:
            os.environ.pop("PIXELBYGGET_HOME", None)
        else:
            os.environ["PIXELBYGGET_HOME"] = self._saved
        self.tmp.cleanup()

    def _archives(self) -> dict[str, bytes]:
        import numpy as np

        # Tile 6404_149: east 149 000 to 150 000, north 6 404 000 to 6 405 000, 0.5 m pixels. The
        # height is the pixel's column plus a thousandth of its row, so a lookup shows which
        # pixel it read. The bottom right pixel is nodata.
        rows, cols = np.mgrid[0:2000, 0:2000]
        values = (cols + rows / 1000.0).astype(np.float32)
        values[1999, 1999] = -9999.0
        return {
            "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_640_14.zip": _city_zip(
                {"640_14/6404_149.tif": _tile_tif(values), "640_14/6404_149.tfw": b"0.5\n"}
            )
        }

    def _ranged(self, archives: dict[str, bytes], calls: list[str]) -> object:
        def fetch(url: str, start: int, end: int) -> tuple[int, int, bytes]:
            calls.append(url)
            data = archives.get(url)
            if data is None:
                return 404, 0, b""
            return 206, len(data), data[start : min(end, len(data) - 1) + 1]

        return fetch

    def test_heights_come_from_the_city_tile_under_the_point(self) -> None:
        from rutor.terrain import load_terrain

        calls: list[str] = []
        fetch = self._ranged(self._archives(), calls)
        # The box reaches into 6406_149, which the city's archive does not have.
        terrain = load_terrain("goteborg-norr", (149000.0, 6404000.0, 150000.0, 6406500.0), ranged=fetch)
        # Pixel centre column 10, row 20: east 149 005.25, north 6 404 989.75.
        self.assertAlmostEqual(terrain.height_rh2000(6404989.75, 149005.25), 10.02, delta=1e-4)
        # Half way between two pixel centres east-west.
        self.assertAlmostEqual(terrain.height_rh2000(6404989.75, 149005.5), 10.52, delta=1e-4)
        # Nodata and a tile the city does not have read as sea level.
        self.assertEqual(terrain.height_rh2000(6404000.25, 149999.75), 0.0)
        self.assertEqual(terrain.height_rh2000(6406200.0, 149500.0), 0.0)
        # A point outside the loaded box is an error, never sea level.
        with self.assertRaises(RutorError):
            terrain.height_rh2000(6420000.0, 149500.0)
        # Above the ellipsoid: the sheet's geoid height on top.
        self.assertAlmostEqual(terrain.height_rh2000(6404989.75, 149005.25) + 36.7, terrain.ellipsoid(6404989.75, 149005.25), delta=1e-9)

    def test_a_tile_is_fetched_once_and_cached(self) -> None:
        from rutor.terrain import load_terrain

        calls: list[str] = []
        fetch = self._ranged(self._archives(), calls)
        load_terrain("goteborg-norr", (149100.0, 6404100.0, 149200.0, 6404200.0), ranged=fetch)
        first = len(calls)
        self.assertGreater(first, 0)
        load_terrain("goteborg-norr", (149100.0, 6404100.0, 149200.0, 6404200.0), ranged=fetch)
        self.assertEqual(len(calls), first)
        cached = Path(self.tmp.name) / "work" / "goteborg-norr" / "sources" / "terrain"
        self.assertTrue((cached / "6404_149.tif").is_file())

    def test_a_missing_tile_is_remembered_as_missing(self) -> None:
        from rutor.terrain import load_terrain

        calls: list[str] = []
        fetch = self._ranged(self._archives(), calls)
        load_terrain("goteborg-norr", (149000.0, 6406000.0, 149500.0, 6406500.0), ranged=fetch)
        first = len(calls)
        load_terrain("goteborg-norr", (149000.0, 6406000.0, 149500.0, 6406500.0), ranged=fetch)
        self.assertEqual(len(calls), first)


class CentreTests(unittest.TestCase):
    def test_the_cell_centre_lands_on_the_window_centre_at_its_ground_height(self) -> None:
        from rutor.render import camera_for

        frame = SheetFrame.from_sheet(_sheet())
        for col, row, ground in ((64, 52, 36.7), (64, 52, 120.0), (3, 110, 40.0), (127, 2, 60.0)):
            camera = camera_for(_sheet(), col, row, ground_ellipsoid_m=ground)
            px, py = frame.map_pixel(camera["center_lat"], camera["center_lon"], ground)
            self.assertAlmostEqual(px, col * 1024 + 512, delta=1e-6)
            self.assertAlmostEqual(py, row * 1024 + 512, delta=1e-6)

    def test_higher_ground_moves_the_centre_south(self) -> None:
        from rutor.render import camera_for

        low = camera_for(_sheet(), 64, 52, ground_ellipsoid_m=36.7)
        high = camera_for(_sheet(), 64, 52, ground_ellipsoid_m=136.7)
        # 100 m higher lifts the ground 100 m up the picture, so the centre is 100 m further south.
        self.assertAlmostEqual((low["center_lat"] - high["center_lat"]) * 111320.0, 100.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
