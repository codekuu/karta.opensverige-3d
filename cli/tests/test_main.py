"""The rutor front: usage, version, and error exit codes."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from rutor.__main__ import main
from rutor.errors import refused


class MainTests(unittest.TestCase):
    def test_no_command_prints_usage_exit_1(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main([])
        self.assertEqual(code, 1)
        self.assertIn("usage", stderr.getvalue().lower())

    def test_rutor_error_maps_to_exit_code(self) -> None:
        def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
            parser = sub.add_parser("boom")

            def run(_args: argparse.Namespace) -> int:
                raise refused("x")

            parser.set_defaults(func=run)

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["boom"], registrars=[register])
        self.assertEqual(code, 2)
        self.assertIn("error: x", stderr.getvalue())

    def test_version_flag(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("0.1.0", stdout.getvalue())
