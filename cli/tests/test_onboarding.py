"""What an agent meets on its way in: output it can read while a command runs, and a refusal
before a lock is taken when the machine cannot render the sheet."""

from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rutor import login
from rutor.__main__ import main
from rutor.config import write_token
from rutor.renderer import google
from tests.fake_service import FakeService
from tests.test_google_render import _installed


def _pipe(encoding: str = "ascii") -> tuple[io.BytesIO, io.TextIOWrapper]:
    """Standard output as an agent's tool sees it: a pipe, block-buffered, and on Windows not UTF-8."""
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding=encoding, line_buffering=False)


class OutputReachesAnAgentTests(unittest.TestCase):
    def test_every_line_is_written_when_it_is_printed(self) -> None:
        raw, out = _pipe()
        with redirect_stdout(out):
            code = main(["--version"])
            # Still inside the program: the line has left already.
            self.assertIn(b"rutor ", raw.getvalue())
            self.assertTrue(out.line_buffering)
            self.assertEqual(out.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual(code, 0)

    def test_the_login_code_is_out_before_the_wait_begins(self) -> None:
        # rutor login prints a code and then waits, for minutes, until a person approves it.
        # An agent has to be able to read the code while the command is still running.
        raw, out = _pipe("utf-8")
        seen: dict[str, bytes] = {}

        def poll(_client_id: str, _device_code: str, _interval: int, _sleep: object) -> str:
            seen["at_wait"] = raw.getvalue()
            return "token-from-github"

        started = {"user_code": "ABCD-1234", "device_code": "device", "interval": 1}
        with (
            patch.object(login, "start_device_flow", return_value=started),
            patch.object(login, "poll_for_token", side_effect=poll),
            patch.object(login, "write_token"),
            patch.object(login, "get", return_value=(200, {"login": "klang"})),
            redirect_stdout(out),
        ):
            code = login.cmd_login(argparse.Namespace(accept_terms=True))
        self.assertEqual(code, 0)
        self.assertIn(b"1. ", seen["at_wait"])
        self.assertIn(b"code: ABCD-1234", seen["at_wait"])
        self.assertIn(b"url: https://github.com/login/device", seen["at_wait"])
        self.assertIn(b"waiting:", seen["at_wait"])


class GoogleSheetReadinessTests(unittest.TestCase):
    def test_each_thing_a_google_sheet_needs_is_checked_on_its_own(self) -> None:
        self.assertIn("CESIUM_ION_TOKEN is not set", google.token_missing({}) or "")
        self.assertIn("CESIUM_ION_TOKEN is not set", google.token_missing({"CESIUM_ION_TOKEN": "  "}) or "")
        self.assertIsNone(google.token_missing({"CESIUM_ION_TOKEN": "t"}))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIn("run npm ci in", google.renderer_missing(root) or "")
            self.assertIsNone(google.renderer_missing(_installed(root)))
        self.assertIn("needs Node 22 or newer on PATH", google.node_missing(which=lambda _name: None) or "")
        old = google.node_missing(which=lambda _name: "/bin/node", run=lambda _argv, _env: (0, "v18.20.0\n", ""))
        self.assertIn("found v18.20.0", old or "")
        self.assertIsNone(google.node_missing(which=lambda _name: "/bin/node", run=lambda _argv, _env: (0, "v22.3.0\n", "")))

    def test_the_first_thing_missing_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _installed(Path(tmp))
            ok = {"which": lambda _name: "/bin/node", "run": lambda _argv, _env: (0, "v22.3.0\n", ""), "render_dir": root}
            self.assertIsNone(google.not_ready(env={"CESIUM_ION_TOKEN": "t"}, **ok))
            self.assertIn("CESIUM_ION_TOKEN is not set", google.not_ready(env={}, **ok) or "")
            no_node = {**ok, "which": lambda _name: None}
            self.assertIn("needs Node 22", google.not_ready(env={"CESIUM_ION_TOKEN": "t"}, **no_node) or "")


class CommandsBeforeALockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeService()
        self._saved = {name: os.environ.get(name) for name in ("PIXELBYGGET_HOME", "PIXELBYGGET_URL")}
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ["PIXELBYGGET_URL"] = self.svc.start()
        write_token("test-token")
        self.svc.state["sheets"] = {
            "stockholm-norr": {"id": "stockholm-norr", "name": "Stockholm", "state": "open", "cols": 4, "rows": 4},
        }
        self.svc.state["cells"] = {
            "stockholm-norr/1_1": {"col": 1, "row": 1, "state": "fri", "login": None, "updated": "", "expires_at": None},
        }

    def tearDown(self) -> None:
        self.svc.stop()
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def run_cmd(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def claims(self) -> list[dict[str, object]]:
        return [r for r in self.svc.requests if r.get("method") == "POST" and r.get("path") == "/v1/claim"]

    def test_no_lock_is_taken_on_a_google_sheet_the_machine_cannot_render(self) -> None:
        # A lock holds the cell for 48 hours. Without Node, the renderer or the token, the
        # render would fail with the cell already taken from everyone else.
        with patch.object(google, "not_ready", return_value="CESIUM_ION_TOKEN is not set. Ask your human to set it."):
            code, out, err = self.run_cmd(["claim", "stockholm-norr", "1_1"])
        self.assertEqual((code, out), (3, ""))
        self.assertIn("this machine cannot render stockholm-norr yet: CESIUM_ION_TOKEN is not set", err)
        self.assertEqual(self.claims(), [])
        with patch.object(google, "not_ready", return_value=None):
            code, out, err = self.run_cmd(["claim", "stockholm-norr", "1_1"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("claimed: stockholm-norr 1_1", out)
        self.assertEqual(len(self.claims()), 1)

    def test_a_machine_that_cannot_build_hears_so_before_it_is_asked_to_log_in(self) -> None:
        # "Log in" is the wrong next step on a machine that can never build a cell.
        from rutor.config import token_path

        token_path().unlink()
        with patch("rutor.commands._gpu_missing", return_value="this is a Mac, and the redraw needs an NVIDIA card"):
            code, _out, err = self.run_cmd(["claim", "stockholm-norr", "1_1"])
        self.assertEqual(code, 3)
        self.assertIn("this machine cannot build a cell: this is a Mac", err)
        self.assertNotIn("rutor login", err)
        self.assertEqual(self.svc.requests, [])

    def test_a_sheet_with_nothing_free_says_so(self) -> None:
        self.svc.state["cells"] = {
            "stockholm-norr/1_1": {"col": 1, "row": 1, "state": "live", "login": "opensverige", "updated": "", "expires_at": None},
        }
        code, out, err = self.run_cmd(["grid", "stockholm-norr", "--free"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "free: none on stockholm-norr\n")

    def test_preflight_can_check_a_sheet_before_any_login(self) -> None:
        machine_ok = (
            patch("rutor.commands._python_missing", return_value=None),
            patch("rutor.commands._gpu_missing", return_value=None),
            patch("rutor.commands._disk_missing", return_value=None),
        )
        sheet = (
            patch.object(google, "node_missing", return_value="the Google source needs Node 22 or newer on PATH"),
            patch.object(google, "renderer_missing", return_value=None),
            patch.object(google, "token_missing", return_value="CESIUM_ION_TOKEN is not set"),
        )
        for p in machine_ok + sheet:
            p.start()
        try:
            code, out, err = self.run_cmd(["preflight", "--machine", "--sheet", "stockholm-norr"])
            self.assertEqual((code, err), (3, ""))
            self.assertEqual(
                out.splitlines(),
                [
                    "check: python ok",
                    "check: gpu ok",
                    "check: disk ok",
                    "check: node missing the Google source needs Node 22 or newer on PATH",
                    "check: google renderer ok",
                    "check: cesium token missing CESIUM_ION_TOKEN is not set",
                ],
            )
            code, out, _err = self.run_cmd(["preflight", "--machine", "--sheet", "goteborg-norr"])
            self.assertEqual(code, 0)
            self.assertEqual(len(out.splitlines()), 3)
            code, out, _err = self.run_cmd(["preflight", "--machine"])
            self.assertEqual((code, len(out.splitlines())), (0, 3))
        finally:
            for p in machine_ok + sheet:
                p.stop()
        # Asking about a sheet is not a login: nothing was sent to the service.
        self.assertEqual(self.svc.requests, [])


if __name__ == "__main__":
    unittest.main()


class ReaderThatStopsTests(unittest.TestCase):
    def test_a_reader_that_stops_reading_ends_the_command_quietly(self) -> None:
        # `rutor grid <sheet> --free | head` closes the pipe after ten lines. That is not a fault.
        class Closed(io.TextIOBase):
            def write(self, _text: str) -> int:
                raise BrokenPipeError

        stderr = io.StringIO()
        with redirect_stdout(Closed()), redirect_stderr(stderr):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
