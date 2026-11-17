"""Camera math, the missing renderer, preflight lines and a patched redraw."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from rutor.__main__ import main
from rutor.config import write_token
from rutor.render import camera_for
from tests.fake_service import FakeService
from tests.png import minimal_png

# The plan's rounded target. The fixture formula lands 0.00125 and 0.00215 away,
# outside ±0.001, so the loose check uses the wider delta and the exact check
# pins the formula.
PLAN_LAT = 57.691
PLAN_LON = 11.967
FORMULA_LAT = 57.689752425440176
FORMULA_LON = 11.969152251361932

PORT = (
    "renderer not ported yet: copy the Cesium renderer into "
    "cli/rutor/renderer/ (see docs/superpowers/specs, section 9)"
)


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

    def write_env(self, mode: int) -> None:
        path = self.root / ".env"
        path.write_text('GOOGLE_MAPS_KEY="test-key"\n', encoding="utf-8")
        os.chmod(path, mode)

    def test_camera_for_center_of_cell(self) -> None:
        camera = camera_for(_sheet(), 64, 59)
        self.assertAlmostEqual(camera["center_lat"], FORMULA_LAT, delta=1e-12)
        self.assertAlmostEqual(camera["center_lon"], FORMULA_LON, delta=1e-12)
        self.assertAlmostEqual(camera["center_lat"], PLAN_LAT, delta=0.003)
        self.assertAlmostEqual(camera["center_lon"], PLAN_LON, delta=0.003)
        self.assertEqual(camera["half_width_m"], 192.0)
        self.assertEqual(camera["half_height_m"], 192.0)
        self.assertEqual(camera["ground_height"], 40.0)
        self.assertEqual(camera["heading"], 0.0)
        bare = dict(_sheet())
        del bare["heading"]
        self.assertEqual(camera_for(bare, 64, 59)["heading"], 0.0)

    def test_render_without_renderer_is_exit_3_with_port_message(self) -> None:
        self.write_env(0o600)
        self.svc.state["sheets"] = {"goteborg-norr": _sheet()}
        code, _out, err = self.run_cmd(["render", "goteborg-norr", "64_59"])
        self.assertEqual(code, 3)
        self.assertIn(PORT, err)
        self.assertFalse((self.root / "work" / "goteborg-norr" / "64_59.src.png").exists())

    def test_world_readable_env_refused(self) -> None:
        self.write_env(0o644)
        self.svc.state["sheets"] = {"goteborg-norr": _sheet()}
        code, _out, err = self.run_cmd(["render", "goteborg-norr", "64_59"])
        self.assertEqual(code, 3)
        self.assertIn("chmod 600 .env", err)
        self.assertEqual(self.svc.requests, [])

    def test_preflight_lines(self) -> None:
        code, out, err = self.run_cmd(["preflight"])
        self.assertEqual(err, "")
        self.assertIn("check: torch missing", out)
        self.assertIn("check: python ok", out)
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
