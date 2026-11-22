"""The Google source: which sheets use it, the camera it hands to Cesium, and the Node call."""

from __future__ import annotations

import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rutor import commands
from rutor.errors import RutorError
from rutor.render import LIFT_M, METRES_PER_PIXEL, WINDOW_PX, camera_for, render_cell
from rutor.renderer import google, sources
from tests.png import minimal_png
from tests.test_review_fixes import ServiceCase

try:
    import numpy
    from PIL import Image

    DRAWING = True
except ImportError:
    DRAWING = False

GOOGLE_SHEET = "stockholm-norr"
CELL = "10_12"
TOKEN = "ion-token-for-tests"


def _sheet(sheet_id: str = GOOGLE_SHEET) -> dict[str, object]:
    return {
        "id": sheet_id,
        "name": "Stockholm",
        "state": "open",
        "cols": 144,
        "rows": 70,
        "origin_lat": 59.334,
        "origin_lon": 18.063,
        "x_meters": -18432,
        "y_meters": 8960,
        "ground_height": 30,
        "heading": 0,
    }


def _installed(root: Path) -> Path:
    """A google-render folder that looks like npm ci has run in it."""
    (root / "node_modules" / "cesium" / "Build" / "Cesium").mkdir(parents=True)
    (root / "node_modules" / "cesium" / "Build" / "Cesium" / "Cesium.js").write_text("", encoding="utf-8")
    (root / "node_modules" / "puppeteer").mkdir(parents=True)
    (root / "render.mjs").write_text("", encoding="utf-8")
    return root


class _Node:
    """Stands in for node: answers --version and writes a PNG where render.mjs would."""

    def __init__(self, picture: bytes | None = None, code: int = 0, stderr: str = "", version: str = "v22.23.4") -> None:
        self.picture = picture
        self.code = code
        self.stderr = stderr
        self.version = version
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        self.calls.append((list(argv), dict(env)))
        if argv[1:] == ["--version"]:
            return 0, self.version + "\n", ""
        if self.code != 0:
            return self.code, "", self.stderr
        out = Path(argv[argv.index("--out") + 1])
        if self.picture is not None:
            out.write_bytes(self.picture)
        return 0, json.dumps({"cesium": "1.147.0", "tiles": 412, "gl": "test"}) + "\n", ""


def _picture(blank: bool = False) -> bytes:
    if blank:
        pixels = numpy.zeros((WINDOW_PX, WINDOW_PX, 4), dtype=numpy.uint8)
        pixels[..., 3] = 255
    else:
        ramp = numpy.linspace(0, 255, WINDOW_PX, dtype=numpy.uint8)
        pixels = numpy.zeros((WINDOW_PX, WINDOW_PX, 4), dtype=numpy.uint8)
        pixels[..., 0] = ramp[None, :]
        pixels[..., 1] = ramp[:, None]
        pixels[..., 3] = 255
    buffer = io.BytesIO()
    Image.fromarray(pixels, "RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


class SheetKindTests(unittest.TestCase):
    def test_goteborg_is_open_data_and_stockholm_is_google(self) -> None:
        self.assertEqual(sources.sheet_kind("goteborg-norr"), "open")
        self.assertEqual(sources.sheet_kind("stockholm-norr"), "google")

    def test_an_unknown_sheet_is_refused(self) -> None:
        with self.assertRaises(RutorError) as caught:
            sources.sheet_kind("malmo-syd")
        self.assertEqual(caught.exception.code, 3)


class GoogleCameraTests(unittest.TestCase):
    def test_the_camera_is_the_cell_window(self) -> None:
        camera = camera_for(_sheet(), 10, 12)
        view = google.google_camera(camera)
        self.assertEqual(view["lat"], camera["center_lat"])
        self.assertEqual(view["lon"], camera["center_lon"])
        self.assertEqual((view["heading_deg"], view["pitch_deg"]), (0.0, -45.0))
        self.assertEqual((view["width"], view["height"]), (WINDOW_PX, WINDOW_PX))
        half = WINDOW_PX / 2 * METRES_PER_PIXEL
        frustum = view["frustum"]
        self.assertEqual((frustum["left"], frustum["right"]), (-half, half))
        self.assertEqual((frustum["bottom"], frustum["top"]), (-half, half))
        self.assertLess(frustum["near"], view["range_m"])
        self.assertGreater(frustum["far"], view["range_m"])

    def test_ground_lands_where_the_open_renderer_draws_it(self) -> None:
        """A point on the ground, north of the cell centre, on the same screen row in both renderers.

        The open renderer puts ground at screen row 768 - (north + lift) * cos45 / 0.25, with the
        ground at the sheet's ground_height. Cesium looks at the target along heading 0 and pitch
        -45, so a point's screen height is its north offset times sin45 plus its up offset times
        cos45, measured from the target.
        """
        sheet = _sheet()
        camera = camera_for(sheet, 10, 12)
        view = google.google_camera(camera)
        t = math.sqrt(0.5)
        for north in (-120.0, 0.0, 85.5):
            up = float(sheet["ground_height"]) - view["height_m"]
            screen_m = north * t + up * t
            row_cesium = WINDOW_PX / 2 - screen_m / METRES_PER_PIXEL
            row_open = WINDOW_PX / 2 - (north + LIFT_M) * t / METRES_PER_PIXEL
            self.assertAlmostEqual(row_cesium, row_open, places=9)


class PinTests(unittest.TestCase):
    def test_the_python_side_names_the_cesium_version_npm_installs(self) -> None:
        package = Path(__file__).resolve().parents[1] / "google-render" / "package.json"
        pinned = json.loads(package.read_text(encoding="utf-8"))["dependencies"]
        self.assertEqual(pinned["cesium"], google.CESIUM_VERSION)
        for name, version in pinned.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", f"{name} is not pinned to one version")


class GoogleRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.renderer = _installed(self.root / "google-render")
        self.out = self.root / "work" / f"{CELL}.src.png"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def render(self, node: _Node, env: dict[str, str] | None = None, which: str | None = "/usr/bin/node") -> dict[str, str]:
        return google.render_google(
            _sheet(),
            10,
            12,
            self.out,
            run=node,
            env={"CESIUM_ION_TOKEN": TOKEN, "PATH": "/usr/bin"} if env is None else env,
            which=lambda _name: which,
            render_dir=self.renderer,
        )

    def test_a_missing_token_says_where_to_get_one(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self.render(_Node(), env={"PATH": "/usr/bin"})
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("CESIUM_ION_TOKEN", caught.exception.message)
        self.assertIn("ion.cesium.com", caught.exception.message)

    def test_a_missing_node_is_refused(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self.render(_Node(), which=None)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("Node 22", caught.exception.message)

    def test_an_old_node_is_refused(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self.render(_Node(version="v18.20.7"))
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("Node 22", caught.exception.message)

    def test_a_renderer_without_npm_ci_is_refused(self) -> None:
        (self.renderer / "node_modules" / "puppeteer").rmdir()
        with self.assertRaises(RutorError) as caught:
            self.render(_Node())
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("npm ci", caught.exception.message)

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_the_token_goes_in_the_environment_never_on_the_command_line(self) -> None:
        node = _Node(_picture())
        self.render(node)
        argv, env = node.calls[-1]
        self.assertNotIn(TOKEN, " ".join(argv))
        self.assertEqual(env["CESIUM_ION_TOKEN"], TOKEN)
        camera_path = Path(argv[argv.index("--camera") + 1])
        self.assertNotIn(TOKEN, camera_path.read_text(encoding="utf-8"))

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_a_render_is_saved_as_a_1536_rgb_png(self) -> None:
        info = self.render(_Node(_picture()))
        with Image.open(self.out) as saved:
            self.assertEqual(saved.size, (WINDOW_PX, WINDOW_PX))
            self.assertEqual(saved.mode, "RGB")
        self.assertEqual(info["cesium"], "1.147.0")

    @unittest.skipUnless(DRAWING, "pillow and numpy are not installed")
    def test_a_blank_render_is_a_network_failure(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self.render(_Node(_picture(blank=True)))
        self.assertEqual(caught.exception.code, 4)
        self.assertFalse(self.out.exists())

    def test_a_wrong_size_is_refused(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self.render(_Node(minimal_png(1024, 1024)))
        self.assertEqual(caught.exception.code, 4)
        self.assertFalse(self.out.exists())

    def test_node_exit_codes_carry_over(self) -> None:
        for node_code, expected in ((4, 4), (3, 3), (1, 3)):
            with self.subTest(node_code=node_code):
                with self.assertRaises(RutorError) as caught:
                    self.render(_Node(code=node_code, stderr="starting\nerror: ion answered 401\n"))
                self.assertEqual(caught.exception.code, expected)
                self.assertIn("ion answered 401", caught.exception.message)


class DispatchTests(unittest.TestCase):
    def test_a_google_sheet_renders_through_cesium(self) -> None:
        with patch.object(google, "render_google") as called, patch("rutor.renderer.render") as open_render:
            render_cell(_sheet(), 10, 12, Path("out.png"))
        called.assert_called_once()
        open_render.assert_not_called()

    def test_an_open_sheet_never_calls_cesium(self) -> None:
        with patch.object(google, "render_google") as called, patch("rutor.renderer.render") as open_render:
            render_cell(_sheet("goteborg-norr"), 10, 12, Path("out.png"))
        called.assert_not_called()
        open_render.assert_called_once()


class GoogleSourceInfoTests(ServiceCase):
    def test_a_google_render_says_so_in_the_source_info_and_on_screen(self) -> None:
        self.svc.state["sheets"] = {GOOGLE_SHEET: _sheet()}

        def fake_render(sheet: dict[str, object], col: int, row: int, out: Path) -> None:
            del sheet, col, row
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(minimal_png(1536, 1536))

        with patch.object(commands, "render_cell", side_effect=fake_render):
            code, out, err = self.run_cmd(["render", GOOGLE_SHEET, CELL])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Google", out)
        self.assertIn("taken down", out)
        info_path = self.root / "work" / GOOGLE_SHEET / f"{CELL}.src.json"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        self.assertEqual(info["provider"], "Google Photorealistic 3D Tiles via Cesium ion asset 2275207")
        self.assertTrue(info["version"].startswith("cesium 1.147.0; rendered "))


if __name__ == "__main__":
    unittest.main()
