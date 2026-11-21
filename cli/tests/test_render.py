"""Camera math, the open-data renderer, preflight lines and a patched redraw."""

from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from rutor.__main__ import main
from rutor.config import write_token
from rutor.errors import RutorError
from rutor.render import camera_for
from rutor.renderer.sources import (
    BACKOFF_SECONDS,
    buildings_query,
    build_request,
    fetch_overpass,
    load_sources,
    trees_query,
    wms_url,
)
from tests.fake_service import FakeService
from tests.png import minimal_png


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
        "state": "open",
        "cols": 128,
        "rows": 128,
        "origin_lat": 57.707,
        "origin_lon": 11.967,
        "x_meters": -16384,
        "y_meters": 13312,
        "ground_height": 40,
        "heading": 0,
        "pipeline": "2026.11-a",
    }


class _FakeImage:
    def __init__(self) -> None:
        self.size = (1536, 1536)

    def save(self, path: object, *args: object, **kwargs: object) -> None:
        del args, kwargs
        Path(str(path)).write_bytes(minimal_png(1536, 1536))


def _install_fake_pil() -> tuple[object, object]:
    image_mod = ModuleType("PIL.Image")
    image_mod.Image = _FakeImage  # type: ignore[attr-defined]
    image_mod.open = lambda path: _FakeImage()  # type: ignore[attr-defined]
    pil_mod = ModuleType("PIL")
    pil_mod.Image = image_mod  # type: ignore[attr-defined]
    previous_pil = sys.modules.get("PIL")
    previous_image = sys.modules.get("PIL.Image")
    sys.modules["PIL"] = pil_mod
    sys.modules["PIL.Image"] = image_mod
    return previous_pil, previous_image


def _restore_pil(previous_pil: object, previous_image: object) -> None:
    if previous_pil is None:
        sys.modules.pop("PIL", None)
    else:
        sys.modules["PIL"] = previous_pil  # type: ignore[assignment]
    if previous_image is None:
        sys.modules.pop("PIL.Image", None)
    else:
        sys.modules["PIL.Image"] = previous_image  # type: ignore[assignment]


def _world_png(url: str) -> bytes:
    """A WMS answer whose pixels encode their own sheet pixel index (goteborg-norr grid)."""
    import numpy as np
    from PIL import Image

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    west, _south, _east, north = (float(part) for part in query["BBOX"][0].split(","))
    width, height = int(query["WIDTH"][0]), int(query["HEIGHT"][0])
    lon_px = 0.25 / (111320.0 * math.cos(math.radians(57.707)))
    lat_px = 0.25 / 111320.0
    gx = round((west - 11.967) / lon_px) + np.arange(width)[None, :]
    gy = round((57.707 - north) / lat_px) + np.arange(height)[:, None]
    rgb = np.stack([(gx * 7 + gy * 13) % 251, (gx * 3) % 251 + 0 * gy, (gy * 5) % 251 + 0 * gx], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.svc = FakeService()
        self._saved = {name: os.environ.get(name) for name in ("PIXELBYGGET_HOME", "PIXELBYGGET_URL")}
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ["PIXELBYGGET_URL"] = self.svc.start()
        write_token("test-token")
        self._root_patch = patch("rutor.config.repo_root", return_value=self.root)
        self._root_patch.start()

    def tearDown(self) -> None:
        self._root_patch.stop()
        self.svc.stop()
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def run_cmd(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_camera_for_center_of_cell(self) -> None:
        camera = camera_for(_sheet(), 64, 52)
        self.assertAlmostEqual(camera["center_lat"], 57.705374, delta=1e-6)
        self.assertAlmostEqual(camera["center_lon"], 11.969152, delta=1e-6)
        self.assertAlmostEqual(camera["south"], 57.70160, delta=2e-5)
        self.assertAlmostEqual(camera["north"], 57.70915, delta=2e-5)
        self.assertAlmostEqual(camera["west"], 11.96478, delta=2e-5)
        self.assertAlmostEqual(camera["east"], 11.97352, delta=2e-5)
        self.assertEqual(camera["half_ew_m"], 260.0)
        self.assertEqual(camera["half_ns_m"], 420.0)
        self.assertEqual(camera["ground_height"], 40.0)
        self.assertEqual(camera["heading"], 0.0)
        self.assertEqual(camera["lift_m"], 34.7)
        self.assertNotIn("half_width_m", camera)
        self.assertNotIn("half_height_m", camera)
        other = camera_for(_sheet(), 60, 54)
        self.assertAlmostEqual(other["center_lat"], 57.698869, delta=1e-6)
        self.assertAlmostEqual(other["center_lon"], 11.951934, delta=1e-6)
        bare = dict(_sheet())
        del bare["heading"]
        self.assertEqual(camera_for(bare, 64, 52)["heading"], 0.0)

    def test_wms_url_for_cell_64_52(self) -> None:
        camera = camera_for(_sheet(), 64, 52)
        url = wms_url(
            "goteborg-norr",
            camera["west"],
            camera["south"],
            camera["east"],
            camera["north"],
            int(camera["ortho_width"]),
            int(camera["ortho_height"]),
        )
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            "https://opengeodata.goteborg.se/services/ortofoto/wms/v1",
        )
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        self.assertEqual(query["SERVICE"], ["WMS"])
        self.assertEqual(query["VERSION"], ["1.3.0"])
        self.assertEqual(query["REQUEST"], ["GetMap"])
        self.assertEqual(query["LAYERS"], ["orto_2025"])
        self.assertEqual(query["CRS"], ["CRS:84"])
        bbox = [float(part) for part in query["BBOX"][0].split(",")]
        for found, wanted in zip(bbox, (camera["west"], camera["south"], camera["east"], camera["north"])):
            self.assertAlmostEqual(found, wanted, delta=1e-9)
        self.assertEqual(query["WIDTH"], ["2080"])
        self.assertEqual(query["HEIGHT"], ["3360"])
        self.assertEqual(query["FORMAT"], ["image/png"])
        box = "(57.70160,11.96478,57.70915,11.97352)"
        buildings = buildings_query(camera["south"], camera["west"], camera["north"], camera["east"])
        self.assertIn(f'way["building"]{box};', buildings)
        self.assertIn(f'way["building:part"]{box};', buildings)
        self.assertIn(f'relation["natural"="water"]{box};', buildings)
        self.assertIn(f'relation["building"]{box};', buildings)
        self.assertTrue(buildings.startswith("[out:json][timeout:90];\n"))
        self.assertTrue(buildings.endswith("out body; >; out skel qt;\n"))
        trees = trees_query(camera["south"], camera["west"], camera["north"], camera["east"])
        self.assertIn(f'node["natural"="tree"]{box};', trees)
        self.assertIn(f'way["natural"~"tree_row|wood|scrub|grassland"]{box};', trees)
        self.assertIn(f'relation["landuse"~"grass|forest"]{box};', trees)

    def test_wms_url_for_a_lat_lon_service_with_its_own_query(self) -> None:
        from rutor.renderer import sources

        lidingo = sources.OrthoSource(
            "https://karta.lidingo.se/wms?servicename=wms_ortofoto_2019_oppendata",
            "theme-ortofoto2019",
            "EPSG:4326",
        )
        with patch.dict(sources.ORTHO_SOURCES, {"test-sheet": lidingo}):
            url = wms_url("test-sheet", 18.13, 59.365, 18.1345, 59.3673, 1024, 1024)
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        self.assertEqual(query["servicename"], ["wms_ortofoto_2019_oppendata"])
        self.assertEqual(query["CRS"], ["EPSG:4326"])
        self.assertEqual(query["LAYERS"], ["theme-ortofoto2019"])
        # EPSG:4326 in WMS 1.3.0 is latitude first: south, west, north, east.
        bbox = [float(part) for part in query["BBOX"][0].split(",")]
        self.assertEqual(bbox, [59.365, 18.13, 59.3673, 18.1345])
        self.assertEqual(url.count("?"), 1)

    def test_neighbour_boxes_share_one_pixel_grid(self) -> None:
        sheet = _sheet()
        lon_px = 0.25 / (111320.0 * math.cos(math.radians(57.707)))
        lat_px = 0.25 / 111320.0
        base = camera_for(sheet, 64, 52)
        for col, row in ((65, 52), (64, 53), (70, 40)):
            other = camera_for(sheet, col, row)
            steps_x = (other["west"] - base["west"]) / lon_px
            steps_y = (other["south"] - base["south"]) / lat_px
            self.assertAlmostEqual(steps_x, round(steps_x), delta=1e-6)
            self.assertAlmostEqual(steps_y, round(steps_y), delta=1e-6)
            self.assertAlmostEqual((other["east"] - other["west"]) / lon_px, 2080.0, delta=1e-6)
            self.assertAlmostEqual((other["north"] - other["south"]) / lat_px, 3360.0, delta=1e-6)
            self.assertEqual(other["ortho_width"], 2080)
            self.assertEqual(other["ortho_height"], 3360)

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_neighbour_cells_agree_in_their_overlap(self) -> None:
        import numpy as np
        from PIL import Image

        from rutor.renderer.draw import draw

        sheet = _sheet()
        origin_lat = 57.707
        origin_lon = 11.967
        lon_px = 0.25 / (111320.0 * math.cos(math.radians(origin_lat)))
        lat_px = 0.25 / 111320.0

        def world_orto(camera: dict[str, float]) -> Image.Image:
            # Pixel values depend only on the world pixel index, so two crops agree.
            gx0 = round((camera["west"] - origin_lon) / lon_px)
            gy0 = round((camera["north"] - origin_lat) / lat_px)
            gx = gx0 + np.arange(int(camera["ortho_width"]))[None, :]
            gy = gy0 - np.arange(int(camera["ortho_height"]))[:, None]
            noise = ((gx * 73856093) ^ (gy * 19349663)) % 97
            busy = ((gx // 200) + (gy // 200)) % 2 == 0
            green = np.where(busy, 60 + noise, 110 + noise // 8)
            rgb = np.stack([green * 0.7, green, green * 0.5], axis=-1).clip(0, 255).astype(np.uint8)
            return Image.fromarray(rgb)

        a = camera_for(sheet, 64, 52)
        b = camera_for(sheet, 65, 52)
        seam_lon = (a["center_lon"] + b["center_lon"]) / 2
        lat = a["center_lat"]
        d_lon = 40.0 / (111320.0 * math.cos(math.radians(lat)))
        d_lat = 40.0 / 111320.0
        nodes = [
            {"type": "node", "id": 1, "lat": lat - d_lat, "lon": seam_lon - d_lon},
            {"type": "node", "id": 2, "lat": lat - d_lat, "lon": seam_lon + d_lon},
            {"type": "node", "id": 3, "lat": lat + d_lat, "lon": seam_lon + d_lon},
            {"type": "node", "id": 4, "lat": lat + d_lat, "lon": seam_lon - d_lon},
            {"type": "node", "id": 5, "lat": lat + 2 * d_lat, "lon": seam_lon},
            {"type": "node", "id": 6, "lat": lat + 2.5 * d_lat, "lon": seam_lon + d_lon},
        ]
        trees = {
            "elements": nodes
            + [
                {"type": "way", "id": 20, "nodes": [1, 2, 3, 4, 1], "tags": {"landuse": "forest"}},
                {"type": "node", "id": 7, "lat": lat - 2 * d_lat, "lon": seam_lon, "tags": {"natural": "tree"}},
                {"type": "way", "id": 21, "nodes": [5, 6], "tags": {"natural": "tree_row"}},
            ]
        }
        # A tall building across the seam, so its windows must line up in both cells.
        b_lon = 15.0 / (111320.0 * math.cos(math.radians(lat)))
        b_lat = 25.0 / 111320.0
        house = [
            {"type": "node", "id": 101, "lat": lat - 3 * d_lat, "lon": seam_lon - b_lon},
            {"type": "node", "id": 102, "lat": lat - 3 * d_lat, "lon": seam_lon + b_lon},
            {"type": "node", "id": 103, "lat": lat - 3 * d_lat + b_lat, "lon": seam_lon + b_lon},
            {"type": "node", "id": 104, "lat": lat - 3 * d_lat + b_lat, "lon": seam_lon - b_lon},
        ]
        osm: dict[str, object] = {
            "elements": house
            + [{"type": "way", "id": 100, "nodes": [101, 102, 103, 104, 101], "tags": {"building": "yes", "height": "24"}}]
            # A tower on the same rows, inside the east cell's photo but outside the west cell's.
            + [
                {"type": "node", "id": 201, "lat": lat - 3 * d_lat, "lon": seam_lon + 5 * b_lon * 2.6},
                {"type": "node", "id": 202, "lat": lat - 3 * d_lat, "lon": seam_lon + 6 * b_lon * 2.6},
                {"type": "node", "id": 203, "lat": lat - 3 * d_lat + b_lat, "lon": seam_lon + 6 * b_lon * 2.6},
                {"type": "node", "id": 204, "lat": lat - 3 * d_lat + b_lat, "lon": seam_lon + 5 * b_lon * 2.6},
                {"type": "way", "id": 200, "nodes": [201, 202, 203, 204, 201], "tags": {"building": "yes", "height": "70"}},
            ]
        }
        images = []
        for camera in (a, b):
            image = draw(
                world_orto(camera),
                osm,
                trees,
                camera["center_lat"],
                camera["center_lon"],
                camera["west"],
                camera["south"],
                camera["east"],
                camera["north"],
                camera["lift_m"],
                grid_lat=origin_lat,
                sheet_x0=int(camera["window_x0"]),
            )
            images.append(np.asarray(image.convert("RGB")).astype(np.int16))
        left = images[0][:, 1024:1536]
        right = images[1][:, 0:512]
        differing = int((np.abs(left - right).sum(axis=-1) != 0).sum())
        self.assertEqual(differing, 0, f"{differing} pixels differ in the east-west overlap")

        # The cell to the south overlaps the bottom third of this window.
        c = camera_for(sheet, 64, 53)
        below = draw(
            world_orto(c),
            osm,
            trees,
            c["center_lat"],
            c["center_lon"],
            c["west"],
            c["south"],
            c["east"],
            c["north"],
            c["lift_m"],
            grid_lat=origin_lat,
            sheet_x0=int(c["window_x0"]),
        )
        lower = np.asarray(below.convert("RGB")).astype(np.int16)
        differing_ns = int((np.abs(images[0][1024:1536] - lower[0:512]).sum(axis=-1) != 0).sum())
        self.assertEqual(differing_ns, 0, f"{differing_ns} pixels differ in the north-south overlap")

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_tree_areas_from_relations_and_repeated_ways(self) -> None:
        import numpy as np
        from PIL import Image

        from rutor.renderer.draw import draw

        camera = camera_for(_sheet(), 64, 52)
        lat, lon = camera["center_lat"], camera["center_lon"]
        d_lon = 60.0 / (111320.0 * math.cos(math.radians(lat)))
        d_lat = 60.0 / 111320.0
        ring = [
            {"type": "node", "id": 1, "lat": lat - d_lat, "lon": lon - d_lon},
            {"type": "node", "id": 2, "lat": lat - d_lat, "lon": lon + d_lon},
            {"type": "node", "id": 3, "lat": lat + d_lat, "lon": lon + d_lon},
            {"type": "node", "id": 4, "lat": lat + d_lat, "lon": lon - d_lon},
        ]
        orto = Image.new("RGB", (int(camera["ortho_width"]), int(camera["ortho_height"])), (120, 120, 110))

        def render(trees: dict[str, object]) -> np.ndarray:
            image = draw(
                orto, {"elements": []}, trees, lat, lon,
                camera["west"], camera["south"], camera["east"], camera["north"], camera["lift_m"],
                grid_lat=57.707, sheet_x0=int(camera["window_x0"]),
            )
            return np.asarray(image.convert("RGB")).astype(np.int16)

        bare = render({"elements": ring + [{"type": "way", "id": 30, "nodes": [1, 2, 3, 4, 1]}]})
        # A forest mapped as a multipolygon: the tags sit on the relation, the way is untagged.
        relation = render({
            "elements": ring
            + [
                {"type": "way", "id": 30, "nodes": [1, 2, 3, 4, 1]},
                {"type": "relation", "id": 40, "tags": {"type": "multipolygon", "landuse": "forest"},
                 "members": [{"type": "way", "ref": 30, "role": "outer"}]},
            ]
        })
        self.assertGreater(int((np.abs(relation - bare).sum(axis=-1) != 0).sum()), 1000)
        # Overpass may send the same way twice, tagged and then as a bare member: tags must win.
        tagged = {"type": "way", "id": 30, "nodes": [1, 2, 3, 4, 1], "tags": {"landuse": "forest"}}
        once = render({"elements": ring + [tagged]})
        twice = render({"elements": ring + [tagged, {"type": "way", "id": 30, "nodes": [1, 2, 3, 4, 1]}]})
        self.assertEqual(int((np.abs(once - twice).sum(axis=-1) != 0).sum()), 0)

    def test_unknown_sheet_is_exit_3(self) -> None:
        sheet = _sheet()
        sheet["id"] = "malmo-norr"
        self.svc.state["sheets"] = {"malmo-norr": sheet}
        code, _out, err = self.run_cmd(["render", "malmo-norr", "1_1"])
        self.assertEqual(code, 3)
        self.assertIn("no open orthophoto source for sheet malmo-norr yet", err)
        self.assertFalse((self.root / "work" / "malmo-norr" / "1_1.src.png").exists())

    def test_overpass_gives_up_after_five_tries(self) -> None:
        calls = {"n": 0}

        def fetch(url: str, data: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
            del url, data, timeout
            calls["n"] += 1
            return 502, b"bad gateway"

        with patch("rutor.renderer.sources.time.sleep") as slept:
            with self.assertRaises(RutorError) as caught:
                fetch_overpass("[out:json];out;", fetch=fetch)
        self.assertEqual(calls["n"], 5)
        self.assertEqual(caught.exception.code, 4)
        self.assertIn("gave up after 5 tries", caught.exception.message)
        self.assertIn("HTTP 502", caught.exception.message)
        self.assertEqual([call.args[0] for call in slept.call_args_list], list(BACKOFF_SECONDS[:4]))

    def test_overpass_request_sends_user_agent(self) -> None:
        request = build_request("https://overpass-api.de/api/interpreter", b"data=query")
        self.assertEqual(request.get_header("User-agent"), "pixelbygget-rutor/0.1")

    def test_cached_sources_are_reused(self) -> None:
        calls = {"n": 0}

        def fetch(url: str, data: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
            del timeout
            calls["n"] += 1
            if data is None:
                return 200, _world_png(url)
            return 200, b'{"elements":[]}'

        camera = camera_for(_sheet(), 64, 52)
        load_sources("goteborg-norr", 64, 52, camera, fetch=fetch)
        # 3 x 4 photo tiles plus two Overpass queries.
        self.assertEqual(calls["n"], 14)
        load_sources("goteborg-norr", 64, 52, camera, fetch=fetch)
        self.assertEqual(calls["n"], 14)
        cache = self.root / "work" / "goteborg-norr" / "sources" / "64_52"
        self.assertTrue((cache / "orto.png").is_file())
        self.assertTrue((cache / "osm.json").is_file())
        self.assertTrue((cache / "trees.json").is_file())

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_neighbours_share_photo_tiles_and_pixels(self) -> None:
        import numpy as np
        from PIL import Image

        wms_calls: list[str] = []

        def fetch(url: str, data: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
            del timeout
            if data is None:
                wms_calls.append(url)
                return 200, _world_png(url)
            return 200, b'{"elements":[]}'

        first = camera_for(_sheet(), 64, 52)
        second = camera_for(_sheet(), 65, 52)
        path_a, _osm_a, _trees_a = load_sources("goteborg-norr", 64, 52, first, fetch=fetch)
        after_first = len(wms_calls)
        path_b, _osm_b, _trees_b = load_sources("goteborg-norr", 65, 52, second, fetch=fetch)
        for url in wms_calls:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertEqual(query["WIDTH"], ["1024"])
            self.assertEqual(query["HEIGHT"], ["1024"])
        self.assertLess(len(wms_calls) - after_first, after_first)
        with Image.open(path_a) as opened:
            a = np.asarray(opened.convert("RGB"))
        with Image.open(path_b) as opened:
            b = np.asarray(opened.convert("RGB"))
        self.assertEqual(a.shape, (3360, 2080, 3))
        self.assertTrue((a[:, 1024:] == b[:, : 2080 - 1024]).all())
        # The top-left pixel is the sheet pixel the snapped box starts at.
        lon_px = 0.25 / (111320.0 * math.cos(math.radians(57.707)))
        lat_px = 0.25 / 111320.0
        gx = round((first["west"] - 11.967) / lon_px)
        gy = round((57.707 - first["north"]) / lat_px)
        self.assertEqual(tuple(int(v) for v in a[0, 0]), ((gx * 7 + gy * 13) % 251, (gx * 3) % 251, (gy * 5) % 251))

    def test_wms_error_page_is_refused(self) -> None:
        def fetch(url: str, data: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
            del url, timeout
            if data is None:
                return 200, b"<?xml version='1.0'?><ServiceExceptionReport/>"
            return 200, b'{"elements":[]}'

        camera = camera_for(_sheet(), 64, 52)
        with self.assertRaises(RutorError) as caught:
            load_sources("goteborg-norr", 64, 52, camera, fetch=fetch)
        self.assertIn("did not return a PNG", caught.exception.message)
        cache = self.root / "work" / "goteborg-norr" / "sources" / "64_52"
        self.assertFalse((cache / "orto.png").exists())

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_render_without_network_uses_injected_fetch(self) -> None:
        from PIL import Image

        self.svc.state["sheets"] = {"goteborg-norr": _sheet()}
        calls = {"n": 0}

        def fetch(url: str, data: bytes | None = None, timeout: float = 120.0) -> tuple[int, bytes]:
            del timeout
            calls["n"] += 1
            if data is None:
                return 200, _world_png(url)
            return 200, b'{"elements":[]}'

        with patch("rutor.renderer.sources.http_request", side_effect=fetch):
            code, out, err = self.run_cmd(["render", "goteborg-norr", "64_52"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("rendered: goteborg-norr 64_52", out)
        self.assertEqual(calls["n"], 14)
        png = self.root / "work" / "goteborg-norr" / "64_52.src.png"
        with Image.open(png) as image:
            self.assertEqual(image.size, (1536, 1536))
            self.assertIn(image.mode, ("RGB", "RGBA"))

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_draw_is_1536_and_deterministic(self) -> None:
        from PIL import Image

        from rutor.renderer.draw import draw

        orto = Image.new("RGB", (24, 24), (150, 140, 120))
        for x in range(24):
            for y in range(0, 24, 3):
                orto.putpixel((x, y), (180 + x, 100, 80))
        west, south, east, north = 11.97000, 57.70500, 11.97040, 57.70530
        lat, lon = 57.70515, 11.97020
        osm = {
            "elements": [
                {"type": "node", "id": 1, "lat": 57.70510, "lon": 11.97010},
                {"type": "node", "id": 2, "lat": 57.70510, "lon": 11.97030},
                {"type": "node", "id": 3, "lat": 57.70522, "lon": 11.97030},
                {"type": "node", "id": 4, "lat": 57.70522, "lon": 11.97010},
                {
                    "type": "way",
                    "id": 9,
                    "nodes": [1, 2, 3, 4, 1],
                    "tags": {"building": "yes", "building:levels": "4"},
                },
            ]
        }
        trees: dict[str, object] = {"elements": []}
        first = draw(orto, osm, trees, lat, lon, west, south, east, north, 34.7)
        second = draw(orto, osm, trees, lat, lon, west, south, east, north, 34.7)
        self.assertEqual(first.size, (1536, 1536))
        self.assertIn(first.mode, ("RGB", "RGBA"))
        self.assertEqual(first.tobytes(), second.tobytes())

    def test_preflight_lines(self) -> None:
        code, out, err = self.run_cmd(["preflight"])
        self.assertEqual(err, "")
        self.assertIn("check: torch missing", out)
        self.assertIn("check: python ok", out)
        self.assertNotIn("check: env", out)
        self.assertEqual(code, 3)

    def test_preflight_checks_render_libraries_not_chrome(self) -> None:
        code, out, _err = self.run_cmd(["preflight"])
        self.assertNotIn("check: chrome", out)
        self.assertRegex(out, r"check: render (ok|missing)")
        self.assertEqual(code, 3)

    def test_redraw_writes_png_and_manifest(self) -> None:
        self.svc.state["sheets"] = {"goteborg-norr": _sheet()}
        work = self.root / "work" / "goteborg-norr"
        work.mkdir(parents=True)
        source = work / "64_59.src.png"
        source.write_bytes(minimal_png(1536, 1536))
        seen: dict[str, object] = {}

        def fake_redraw(image: object, prompt: str, seed: int | None) -> _FakeImage:
            del image
            seen["prompt"] = prompt
            seen["seed"] = seed
            return _FakeImage()

        previous = _install_fake_pil()
        try:
            with (
                patch("rutor.redraw_core.preflight_message", return_value=None),
                patch("rutor.redraw_core.redraw_image", side_effect=fake_redraw),
                patch("rutor.commands.check_problems", return_value=[]),
            ):
                code, out, err = self.run_cmd(["redraw", "goteborg-norr", "64_59", "--seed", "7"])
        finally:
            _restore_pil(previous[0], previous[1])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("pin: unset", out)
        self.assertEqual(
            seen["prompt"],
            "Repaint this 3D map render as mndfstyle isometric pixel-art map of "
            "Göteborg, Sweden, keeping every road, building and tree in place",
        )
        self.assertEqual(seen["seed"], 7)
        png = work / "64_59.png"
        manifest_path = work / "64_59.manifest.json"
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["pipeline"], "2026.11-a")
        self.assertEqual(manifest["seed"], 7)
        self.assertEqual(manifest["width"], 1536)
        self.assertEqual(manifest["height"], 1536)
        self.assertEqual(manifest["source"], {"provider": "unknown", "version": "unknown"})
        self.assertEqual(manifest["gpu"], "unknown")
        self.assertRegex(manifest["renderedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(len(manifest["sha256"]), 64)
        self.assertEqual(manifest["check"], "ok")
        self.assertEqual(manifest["check_problems"], [])
