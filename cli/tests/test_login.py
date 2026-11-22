"""GitHub device flow: polling, the client id, and the terms."""

from __future__ import annotations

import io
import os
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from rutor.__main__ import main
from rutor.config import github_client_id
from rutor.errors import RutorError
from rutor.login import TERMS, poll_for_token, start_device_flow

Http = Callable[[str, dict[str, str]], dict[str, object]]


class LoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("PIXELBYGGET_GITHUB_CLIENT_ID")
        os.environ.pop("PIXELBYGGET_GITHUB_CLIENT_ID", None)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("PIXELBYGGET_GITHUB_CLIENT_ID", None)
        else:
            os.environ["PIXELBYGGET_GITHUB_CLIENT_ID"] = self._old

    def test_start_returns_codes(self) -> None:
        seen: dict[str, object] = {}

        def http(url: str, form: dict[str, str]) -> dict[str, object]:
            seen["url"] = url
            seen["form"] = dict(form)
            return {
                "device_code": "dev",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            }

        result = start_device_flow("client", http=http)
        self.assertEqual(result["device_code"], "dev")
        self.assertEqual(result["user_code"], "ABCD-1234")
        self.assertEqual(result["interval"], 5)
        self.assertIn("/login/device/code", str(seen["url"]))
        form = seen["form"]
        self.assertIsInstance(form, dict)
        self.assertEqual(form["client_id"], "client")
        self.assertEqual(form["scope"], "")

    def test_poll_returns_token_when_granted(self) -> None:
        def http(_url: str, _form: dict[str, str]) -> dict[str, object]:
            return {"access_token": "gho_token"}

        sleeps: list[float] = []
        token = poll_for_token("client", "dev", 5, sleeps.append, http=http)
        self.assertEqual(token, "gho_token")
        self.assertEqual(sleeps, [])

    def test_poll_slow_down_backs_off(self) -> None:
        answers: list[dict[str, object]] = [{"error": "slow_down"}, {"access_token": "gho_token"}]

        def http(_url: str, _form: dict[str, str]) -> dict[str, object]:
            return answers.pop(0)

        sleeps: list[float] = []
        interval = 5
        token = poll_for_token("client", "dev", interval, sleeps.append, http=http)
        self.assertEqual(token, "gho_token")
        self.assertEqual(sleeps, [float(interval + 5)])

    def test_poll_authorization_pending_keeps_waiting(self) -> None:
        answers: list[dict[str, object]] = [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            {"access_token": "gho_token"},
        ]

        def http(_url: str, _form: dict[str, str]) -> dict[str, object]:
            return answers.pop(0)

        sleeps: list[float] = []
        token = poll_for_token("client", "dev", 5, sleeps.append, http=http)
        self.assertEqual(token, "gho_token")
        self.assertEqual(sleeps, [5.0, 5.0])

    def test_expired_device_code_exit_3(self) -> None:
        def http(_url: str, _form: dict[str, str]) -> dict[str, object]:
            return {"error": "expired_token"}

        with self.assertRaises(RutorError) as caught:
            poll_for_token("client", "dev", 5, lambda _seconds: None, http=http)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("login", caught.exception.message)

    def test_default_client_id_is_the_pixelbygget_app(self) -> None:
        self.assertEqual(github_client_id(), "Ov23liulid4VoLnmJCVH")

    def test_empty_client_id_refused(self) -> None:
        os.environ["PIXELBYGGET_GITHUB_CLIENT_ID"] = ""
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["login"])
        self.assertEqual(code, 3)
        self.assertIn("PIXELBYGGET_GITHUB_CLIENT_ID", stderr.getvalue())

    def test_login_requires_accept_terms_or_prints_terms(self) -> None:
        os.environ["PIXELBYGGET_GITHUB_CLIENT_ID"] = "Iv1.real"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["login"])
        self.assertEqual(code, 1)
        self.assertIn(TERMS, stdout.getvalue())
        self.assertIn("MIT", TERMS)
