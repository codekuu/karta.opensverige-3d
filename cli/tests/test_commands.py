"""cities, grid, claim, release, status and whoami against the fake service."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from unittest.mock import patch

from rutor import commands
from rutor.__main__ import main
from rutor.config import write_token
from rutor.errors import RutorError
from rutor.renderer import google, sources
from tests.fake_service import FakeService
from tests.png import minimal_png


def _cell(col: int, row: int, state: str, login: str | None = None, expires: str | None = None) -> dict[str, object]:
    return {
        "col": col,
        "row": row,
        "state": state,
        "login": login,
        "updated": "2026-11-17T12:00:00Z",
        "expires_at": expires,
    }


class _ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeService()
        self._saved = {name: os.environ.get(name) for name in ("PIXELBYGGET_HOME", "PIXELBYGGET_URL")}
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ["PIXELBYGGET_URL"] = self.svc.start()
        write_token("test-token")

    def tearDown(self) -> None:
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


class CommandTests(_ServiceCase):
    def open_sheet(self, cells: dict[str, dict[str, object]], **overrides: object) -> None:
        sheet: dict[str, object] = {
            "id": "goteborg-norr",
            "name": "Göteborg",
            "state": "open",
            "done": 1,
            "total": 4,
            "cols": 3,
            "rows": 3,
            "origin_lat": 57.707,
            "origin_lon": 11.967,
            "x_meters": -16384,
            "y_meters": 13312,
            "ground_height": 40,
            "heading": 0,
            "pipeline": "2026.11-a",
        }
        sheet.update(overrides)
        self.svc.state["sheets"] = {"goteborg-norr": sheet}
        self.svc.state["cells"] = cells

    def test_cities_lines(self) -> None:
        self.svc.state["sheets"] = {
            "goteborg-norr": {"id": "goteborg-norr", "state": "open", "done": 1, "total": 4},
            "malmo-norr": {"id": "malmo-norr", "state": "queued", "done": 0, "total": 9},
        }
        code, out, _err = self.run_cmd(["cities"])
        self.assertEqual(code, 0)
        self.assertIn("sheet: goteborg-norr open 1/4\n", out)
        self.assertIn("sheet: malmo-norr queued 0/9\n", out)
        code, raw, _err = self.run_cmd(["cities", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(raw.strip().startswith("{"))
        self.assertIn("goteborg-norr", raw)

    def test_grid_free_filter(self) -> None:
        self.open_sheet(
            {
                "goteborg-norr/0_0": _cell(0, 0, "fri"),
                "goteborg-norr/1_0": _cell(1, 0, "last", "klang"),
            }
        )
        code, out, _err = self.run_cmd(["grid", "goteborg-norr", "--free"])
        self.assertEqual(code, 0)
        self.assertIn("cell: 0_0 fri\n", out)
        self.assertNotIn("1_0", out)
        code, full, _err = self.run_cmd(["grid", "goteborg-norr"])
        self.assertEqual(code, 0)
        self.assertIn("cell: 1_0 last klang\n", full)

    def test_claim_prints_expires(self) -> None:
        self.open_sheet({"goteborg-norr/0_0": _cell(0, 0, "fri")})
        code, out, err = self.run_cmd(["claim", "goteborg-norr", "0_0"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("claimed: goteborg-norr 0_0 expires 2026-11-19T12:00:00Z\n", out)

    def test_claim_taken_is_exit_2(self) -> None:
        self.open_sheet({"goteborg-norr/0_0": _cell(0, 0, "last", "other")})
        code, _out, err = self.run_cmd(["claim", "goteborg-norr", "0_0"])
        self.assertEqual(code, 2)
        self.assertIn("refused: taken:", err)

    def test_next_prefers_cell_next_to_live(self) -> None:
        self.open_sheet(
            {
                "goteborg-norr/2_2": _cell(2, 2, "fri"),
                "goteborg-norr/0_0": _cell(0, 0, "live", "gustaf"),
                "goteborg-norr/0_1": _cell(0, 1, "fri"),
            },
            cols=5,
            rows=5,
        )
        code, out, err = self.run_cmd(["claim", "goteborg-norr", "--next"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("claimed: goteborg-norr 0_1 expires 2026-11-19T12:00:00Z\n", out)

    def test_next_falls_back_then_refuses(self) -> None:
        self.open_sheet(
            {
                "goteborg-norr/0_0": _cell(0, 0, "fri"),
                "goteborg-norr/1_1": _cell(1, 1, "fri"),
                "goteborg-norr/2_2": _cell(2, 2, "fri"),
            }
        )
        code, out, err = self.run_cmd(["claim", "goteborg-norr", "--next"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("claimed: goteborg-norr 1_1 expires", out)
        self.svc.state["cells"] = {
            "goteborg-norr/0_0": _cell(0, 0, "live", "gustaf"),
            "goteborg-norr/1_1": _cell(1, 1, "ute"),
        }
        code, _out, err = self.run_cmd(["claim", "goteborg-norr", "--next"])
        self.assertEqual(code, 2)
        self.assertIn("refused: no_free_cell: no cell on goteborg-norr is free. rutor cities shows where there are free cells.", err)

    def test_release_ok(self) -> None:
        self.open_sheet({"goteborg-norr/1_0": _cell(1, 0, "last", "klang", "2026-11-19T12:00:00Z")})
        code, out, err = self.run_cmd(["release", "goteborg-norr", "1_0"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("released: goteborg-norr 1_0\n", out)
        cells = self.svc.state["cells"]
        self.assertIsInstance(cells, dict)
        self.assertEqual(cells["goteborg-norr/1_0"]["state"], "fri")

    def test_status_lists_locks(self) -> None:
        self.open_sheet(
            {"goteborg-norr/1_0": _cell(1, 0, "last", "klang", "2026-11-19T12:00:00Z")}
        )
        code, out, err = self.run_cmd(["status"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("lock: goteborg-norr 1_0 expires 2026-11-19T12:00:00Z\n", out)
        code, who, err = self.run_cmd(["whoami"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("login: klang\n", who)

    def test_commands_need_login(self) -> None:
        token = os.path.join(self.tmp.name, "token")
        os.remove(token)
        # The lists are public; taking a cell is not.
        code, _out, err = self.run_cmd(["cities"])
        self.assertEqual((code, err), (0, ""))
        # The sheet is there to be read: a claim reads its row, which is public, before it asks
        # for the login.
        self.open_sheet({"goteborg-norr/0_0": _cell(0, 0, "fri")})
        code, _out, err = self.run_cmd(["claim", "goteborg-norr", "0_0"])
        self.assertEqual(code, 3)
        self.assertIn("not logged in: run rutor login", err)

    def test_queue_lines(self) -> None:
        self.svc.state["queue"] = [
            {
                "sheet": "goteborg-norr",
                "col": 0,
                "row": 1,
                "state": "vantar",
                "owner_login": "klang",
                "uploaded_at": "2026-11-17T12:00:00Z",
            },
            {
                "sheet": "goteborg-norr",
                "col": 2,
                "row": 2,
                "state": "godkand",
                "owner_login": "other",
                "uploaded_at": "2026-11-17T13:00:00Z",
            },
        ]
        code, out, err = self.run_cmd(["queue"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("waiting: goteborg-norr 0_1 klang uploaded 2026-11-17T12:00:00Z\n", out)
        self.assertNotIn("other", out)
        code, out, err = self.run_cmd(["queue", "--state", "godkand"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("approved: goteborg-norr 2_2 other uploaded 2026-11-17T13:00:00Z\n", out)
        self.assertNotIn("waiting:", out)

    def test_queue_show_downloads_png(self) -> None:
        png = minimal_png(8, 8)
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": png}
        self.svc.state["queue"] = [
            {
                "sheet": "goteborg-norr",
                "col": 0,
                "row": 1,
                "state": "vantar",
                "owner_login": "klang",
                "uploaded_at": "2026-11-17T12:00:00Z",
            }
        ]
        dest = Path(self.tmp.name) / "show"
        code, out, err = self.run_cmd(["queue", "--show", str(dest)])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("waiting: goteborg-norr 0_1 klang uploaded 2026-11-17T12:00:00Z\n", out)
        self.assertEqual((dest / "goteborg-norr_0_1.png").read_bytes(), png)

    def test_approve_ok(self) -> None:
        code, out, err = self.run_cmd(["approve", "goteborg-norr", "0_1"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("approved: goteborg-norr 0_1\n", out)
        posts = [item for item in self.svc.requests if item.get("method") == "POST" and item.get("path") == "/v1/approve"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(json.loads(posts[0]["body"]), {"sheet": "goteborg-norr", "col": 0, "row": 1})

    def test_reject_requires_comment(self) -> None:
        code, _out, err = self.run_cmd(["reject", "goteborg-norr", "0_1"])
        self.assertEqual(code, 1)
        self.assertIn("comment", err)
        self.assertEqual(self.svc.requests, [])

    def test_trust_and_off(self) -> None:
        code, out, err = self.run_cmd(["trust", "gustaf"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("trust: gustaf on\n", out)
        sent = json.loads(self.svc.requests[-1]["body"])
        self.assertEqual(sent, {"login": "gustaf", "trusted": 1})
        code, out, err = self.run_cmd(["trust", "gustaf", "--off"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("trust: gustaf off\n", out)
        sent = json.loads(self.svc.requests[-1]["body"])
        self.assertEqual(sent, {"login": "gustaf", "trusted": 0})

    def test_admin_refused_is_exit_2(self) -> None:
        self.svc.fail_next_with(
            403,
            json.dumps({"error": "not_admin", "message": "Admin only."}).encode("utf-8"),
            "application/json",
        )
        code, _out, err = self.run_cmd(["approve", "goteborg-norr", "0_0"])
        self.assertEqual(code, 2)
        self.assertIn("refused: not_admin:", err)


NEW_SHEET = "se-n5602-e1262"


def _new_sheet(**overrides: object) -> dict[str, object]:
    """A sheet of the tiling as the service answers it: the drawing code has no line for its id."""
    sheet: dict[str, object] = {
        "id": NEW_SHEET,
        "name": "N 56,02 E 12,62",
        "state": "open",
        "done": 0,
        "total": 0,
        "cols": 61,
        "rows": 47,
        "origin_lat": 56.025,
        "origin_lon": 12.625,
        "x_meters": -7808,
        "y_meters": 6064.08,
        "ground_height": 68,
        "pipeline": "2026.11-a",
        "kind": "google",
        "made": "grid",
    }
    sheet.update(overrides)
    return sheet


class KindOfTests(unittest.TestCase):
    def test_the_sheet_row_says_the_source(self) -> None:
        self.assertEqual(sources.kind_of({"id": NEW_SHEET, "kind": "google"}), "google")
        self.assertEqual(sources.kind_of({"id": "any-town", "kind": "open"}), "open")
        # The row is the service's word, and it wins over the line the code still carries.
        self.assertEqual(sources.kind_of({"id": "stockholm-norr", "kind": "open"}), "open")

    def test_a_row_from_a_service_that_says_no_source_falls_back_on_the_two_known_sheets(self) -> None:
        self.assertEqual(sources.kind_of({"id": "stockholm-norr"}), "google")
        self.assertEqual(sources.kind_of({"id": "goteborg-norr"}), "open")
        self.assertEqual(sources.kind_of({"id": "stockholm-norr", "kind": "bing"}), "google")
        self.assertEqual(sources.kind_of({"id": "stockholm-norr", "kind": 7}), "google")

    def test_no_source_at_all_is_refused_by_name(self) -> None:
        for sheet in ({"id": NEW_SHEET}, {"id": NEW_SHEET, "kind": "bing"}, {"id": NEW_SHEET, "kind": None}, {}):
            with self.subTest(sheet=sheet), self.assertRaises(RutorError) as caught:
                sources.kind_of(sheet)
            self.assertEqual(caught.exception.code, 3)
        with self.assertRaises(RutorError) as caught:
            sources.kind_of({"id": NEW_SHEET})
        self.assertIn(f"no source decided for sheet {NEW_SHEET} yet", caught.exception.message)


class SourceFromTheServiceTests(_ServiceCase):
    """The source of a sheet is the service's to say, so a sheet nobody wrote into the code can be drawn."""

    def setUp(self) -> None:
        super().setUp()
        # A render writes under the repository's work folder. Here that is the temp folder.
        root = patch("rutor.config.repo_root", return_value=Path(self.tmp.name))
        root.start()
        self.addCleanup(root.stop)

    def claims(self) -> list[dict[str, object]]:
        return [r for r in self.svc.requests if r.get("method") == "POST" and r.get("path") == "/v1/claim"]

    def put(self, sheet: dict[str, object]) -> None:
        self.svc.state["sheets"] = {str(sheet["id"]): sheet}
        self.svc.state["cells"] = {f"{sheet['id']}/3_4": _cell(3, 4, "fri")}

    def test_a_google_sheet_with_an_unknown_id_renders_through_the_google_path(self) -> None:
        self.put(_new_sheet())
        seen: list[tuple[object, int, int]] = []

        def fake_google(sheet: dict[str, object], col: int, row: int, out: Path) -> None:
            seen.append((sheet.get("id"), col, row))
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(minimal_png(1536, 1536))

        with patch.object(google, "render_google", side_effect=fake_google), patch(
            "rutor.renderer.render", side_effect=AssertionError("the open renderer was asked")
        ):
            code, out, err = self.run_cmd(["render", NEW_SHEET, "3_4"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(seen, [(NEW_SHEET, 3, 4)])
        self.assertIn("note: This sheet is drawn from Google Photorealistic 3D Tiles", out)
        info = json.loads((Path(self.tmp.name) / "work" / NEW_SHEET / "3_4.src.json").read_text(encoding="utf-8"))
        self.assertTrue(info["provider"].startswith("Google Photorealistic 3D Tiles"), info)

    def test_a_sheet_with_no_source_is_not_claimed(self) -> None:
        self.put(_new_sheet(kind=None))
        code, out, err = self.run_cmd(["claim", NEW_SHEET, "3_4"])
        self.assertEqual((code, out), (3, ""))
        self.assertIn(f"no source decided for sheet {NEW_SHEET} yet", err)
        self.assertEqual(self.claims(), [])
        code, _out, err = self.run_cmd(["claim", NEW_SHEET, "--next"])
        self.assertEqual(code, 3)
        self.assertEqual(self.claims(), [])

    def test_a_sheet_with_no_source_is_not_rendered(self) -> None:
        self.put(_new_sheet(kind=None))
        with patch.object(commands, "render_cell", side_effect=AssertionError("a renderer was asked")):
            code, _out, err = self.run_cmd(["render", NEW_SHEET, "3_4"])
        self.assertEqual(code, 3)
        self.assertIn("no source decided", err)

    def test_stockholm_is_google_also_when_the_service_does_not_say(self) -> None:
        self.put(_new_sheet(id="stockholm-norr", kind=None))
        with patch.object(google, "not_ready", return_value="CESIUM_ION_TOKEN is not set."):
            code, _out, err = self.run_cmd(["claim", "stockholm-norr", "3_4"])
        self.assertEqual(code, 3)
        self.assertIn("this machine cannot render stockholm-norr yet: CESIUM_ION_TOKEN is not set", err)
        self.assertEqual(self.claims(), [])

    def test_a_google_sheet_with_an_unknown_id_is_not_claimed_on_a_machine_that_cannot_render_it(self) -> None:
        self.put(_new_sheet())
        with patch.object(google, "not_ready", return_value="the Google renderer is not installed."):
            code, _out, err = self.run_cmd(["claim", NEW_SHEET, "3_4"])
        self.assertEqual(code, 3)
        self.assertIn(f"this machine cannot render {NEW_SHEET} yet: the Google renderer is not installed", err)
        self.assertEqual(self.claims(), [])
        with patch.object(google, "not_ready", return_value=None):
            code, out, err = self.run_cmd(["claim", NEW_SHEET, "3_4"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"claimed: {NEW_SHEET} 3_4", out)
        self.assertEqual(len(self.claims()), 1)

    def test_an_open_sheet_asks_nothing_of_google(self) -> None:
        self.put(_new_sheet(id="any-town", kind="open"))
        with patch.object(google, "not_ready", side_effect=AssertionError("google was asked")):
            code, _out, err = self.run_cmd(["claim", "any-town", "3_4"])
        self.assertEqual((code, err), (0, ""))

    def test_preflight_asks_the_service_which_source_an_unknown_sheet_has(self) -> None:
        self.put(_new_sheet())
        machine_ok = (
            patch("rutor.commands._python_missing", return_value=None),
            patch("rutor.commands._disk_missing", return_value=None),
            patch.object(google, "node_missing", return_value=None),
            patch.object(google, "renderer_missing", return_value=None),
            patch.object(google, "token_unusable", return_value="CESIUM_ION_TOKEN is not set"),
        )
        for p in machine_ok:
            p.start()
            self.addCleanup(p.stop)
        code, out, err = self.run_cmd(["preflight", "--machine", "--sheet", NEW_SHEET])
        self.assertEqual((code, err), (3, ""))
        self.assertEqual(
            out.splitlines()[3:],
            ["check: node ok", "check: google renderer ok", "check: cesium token missing CESIUM_ION_TOKEN is not set"],
        )
        # A sheet nobody knows the source of is said, not passed over.
        code, out, _err = self.run_cmd(["preflight", "--machine", "--sheet", "no-such-sheet"])
        self.assertEqual(code, 3)
        self.assertIn("check: sheet missing ", out)
