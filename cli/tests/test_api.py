"""The service client against the in-process fake."""

from __future__ import annotations

import json
import os
import socket
import unittest

from rutor.api import get, put_bytes
from rutor.errors import RutorError
from tests.fake_service import FakeService


def header(request: dict[str, object], name: str) -> str | None:
    headers = request["headers"]
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return None


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_url = os.environ.get("PIXELBYGGET_URL")
        self.svc = FakeService()
        os.environ["PIXELBYGGET_URL"] = self.svc.start()

    def tearDown(self) -> None:
        self.svc.stop()
        if self._old_url is None:
            os.environ.pop("PIXELBYGGET_URL", None)
        else:
            os.environ["PIXELBYGGET_URL"] = self._old_url

    def test_get_parses_json(self) -> None:
        status, body = get("/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "service": "pixelbygget"})

    def test_bearer_header_sent(self) -> None:
        get("/v1/me", token="secret")
        self.assertEqual(header(self.svc.requests[0], "Authorization"), "Bearer secret")

    def test_refused_carries_service_code(self) -> None:
        self.svc.fail_next_with(
            409,
            json.dumps({"error": "taken", "message": "Someone else holds that cell."}).encode(),
            "application/json",
        )
        with self.assertRaises(RutorError) as caught:
            get("/v1/health")
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(caught.exception.message, "refused: taken: Someone else holds that cell.")

    def test_non_json_answer_is_exit_4(self) -> None:
        self.svc.fail_next_with(502, b"<html>bad gateway</html>", "text/html")
        with self.assertRaises(RutorError) as caught:
            get("/v1/health")
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(caught.exception.message, "service answered 502, not JSON")

    def test_connection_refused_is_exit_4(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        os.environ["PIXELBYGGET_URL"] = f"http://127.0.0.1:{port}"
        with self.assertRaises(RutorError) as caught:
            get("/v1/health")
        self.assertEqual(caught.exception.code, 4)

    def test_put_bytes_sends_body_and_headers(self) -> None:
        status, body = put_bytes(
            "/v1/upload/goteborg-norr/1_0",
            b"png-bytes",
            {"X-Manifest": "abc", "Content-Type": "image/png"},
            "token",
        )
        self.assertEqual(status, 201)
        self.assertIsInstance(body, dict)
        request = self.svc.requests[0]
        self.assertEqual(request["body"], b"png-bytes")
        self.assertEqual(header(request, "X-Manifest"), "abc")
        self.assertEqual(header(request, "Content-Type"), "image/png")
