"""cities, grid, claim, release, status and whoami against the fake service."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rutor.__main__ import main
from rutor.config import write_token
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


class CommandTests(unittest.TestCase):
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
        self.assertIn("refused: no_free_cell", err)

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
