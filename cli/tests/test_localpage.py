"""The shell the pages on this machine share: who gets in, and what every answer carries."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import unittest

from rutor import localpage
from rutor.errors import RutorError, bad_input, environment, network, refused
from rutor.localpage import Refusal, Reply


class LocalPageTests(unittest.TestCase):
    def setUp(self) -> None:
        # What the next GET of "raise" throws, so each test picks its own fault.
        self.fault: Exception | None = None
        self.posted: list[bytes] = []
        self.page = localpage.LocalPage("shell-test", self._get, self._post)
        self.page.start()
        self.addCleanup(self.page.stop)
        self.port = self.page.port
        self.base = f"/{self.page.key}"

    def _get(self, rest: str) -> Reply | None:
        if rest == "":
            return Reply(200, "text/plain", b"hej")
        if rest == "raise" and self.fault is not None:
            raise self.fault
        return None

    def _post(self, rest: str, raw: bytes) -> Reply | None:
        if rest != "api/echo":
            return None
        self.posted.append(raw)
        return Reply(200, "application/json; charset=utf-8", raw)

    def call(
        self,
        method: str,
        path: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = dict(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            sent.setdefault("Content-Type", "application/json")
        try:
            connection.request(method, path, body=data, headers=sent)
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    def test_nothing_answers_without_the_key(self) -> None:
        self.assertEqual(self.page.url, f"http://127.0.0.1:{self.port}/{self.page.key}/")
        self.assertGreaterEqual(len(self.page.key), 32)
        status, _headers, body = self.call("GET", f"{self.base}/")
        self.assertEqual((status, body), (200, b"hej"))
        for path in ("/", "/wrong/", f"{self.base}x/", f"{self.base}"):
            status, _headers, body = self.call("GET", path)
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(body), {"error": "not found"}, path)
        status, _headers, _body = self.call("POST", "/wrong/api/echo", {"a": 1})
        self.assertEqual(status, 404)
        self.assertEqual(self.posted, [])
        # A path the page does not know is the same answer, under the key too.
        status, _headers, body = self.call("GET", f"{self.base}/nope")
        self.assertEqual((status, json.loads(body)), (404, {"error": "not found"}))
        status, _headers, _body = self.call("POST", f"{self.base}/api/nope", {"a": 1})
        self.assertEqual(status, 404)

    def test_a_request_under_another_host_name_is_refused(self) -> None:
        # A page elsewhere can point its own name at 127.0.0.1. It cannot fake the Host header.
        for host in (f"evil.test:{self.port}", f"localhost:{self.port}", "127.0.0.1"):
            status, _headers, body = self.call("GET", f"{self.base}/", headers={"Host": host})
            self.assertEqual(status, 403, host)
            self.assertNotIn(b"hej", body)
            status, _headers, _body = self.call("POST", f"{self.base}/api/echo", {"a": 1}, {"Host": host})
            self.assertEqual(status, 403, host)
        self.assertEqual(self.posted, [])
        status, _headers, _body = self.call("GET", f"{self.base}/", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_a_post_from_another_page_is_refused(self) -> None:
        for origin in ("http://evil.test", "null", f"http://localhost:{self.port}", f"https://127.0.0.1:{self.port}"):
            status, _headers, _body = self.call("POST", f"{self.base}/api/echo", {"a": 1}, {"Origin": origin})
            self.assertEqual(status, 403, origin)
        self.assertEqual(self.posted, [])
        status, _headers, body = self.call("POST", f"{self.base}/api/echo", {"a": 1}, {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual((status, json.loads(body)), (200, {"a": 1}))

    def test_a_post_must_be_json_and_small(self) -> None:
        status, _headers, _body = self.call("POST", f"{self.base}/api/echo", b'{"a": 1}', {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _headers, _body = self.call("POST", f"{self.base}/api/echo", b" " * 4097)
        self.assertEqual(status, 400)
        status, _headers, _body = self.call("POST", f"{self.base}/api/echo", b"")
        self.assertEqual(status, 400)
        # No length at all: nothing is read, so a sender cannot hold the page waiting for a body.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest("POST", f"{self.base}/api/echo")
            connection.putheader("Content-Type", "application/json")
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()
        self.assertEqual(self.posted, [])
        status, _headers, _body = self.call("POST", f"{self.base}/api/echo", b" " * 4096)
        self.assertEqual(status, 200)

    def test_a_page_may_ask_for_a_smaller_body(self) -> None:
        small = localpage.LocalPage("shell-test", self._get, self._post, body_max=8)
        small.start()
        self.addCleanup(small.stop)
        connection = http.client.HTTPConnection("127.0.0.1", small.port, timeout=10)
        try:
            connection.request("POST", f"/{small.key}/api/echo", body=b" " * 9, headers={"Content-Type": "application/json"})
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()

    def test_every_answer_locks_the_page_down(self) -> None:
        asked: list[tuple[str, str, object | None, int]] = [
            ("GET", f"{self.base}/", None, 200),
            ("GET", f"{self.base}/nope", None, 404),
            ("GET", "/wrong/", None, 404),
            ("POST", f"{self.base}/api/echo", {"a": 1}, 200),
            ("POST", f"{self.base}/api/echo", b"", 400),
            ("PUT", f"{self.base}/api/echo", {"a": 1}, 405),
        ]
        for method, path, body, expected in asked:
            status, headers, _body = self.call(method, path, body)
            self.assertEqual(status, expected, path)
            self.assertEqual(headers.get("content-security-policy"), localpage.POLICY, path)
            self.assertEqual(headers.get("x-frame-options"), "DENY", path)
            self.assertEqual(headers.get("cache-control"), "no-store", path)
            self.assertEqual(headers.get("referrer-policy"), "no-referrer", path)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", path)
            self.assertNotIn("access-control-allow-origin", headers, path)
        self.assertIn("default-src 'none'", localpage.POLICY)
        self.assertIn("frame-ancestors 'none'", localpage.POLICY)
        self.assertNotIn("unsafe", localpage.POLICY)
        self.assertNotIn("http", localpage.POLICY)

    def test_only_get_and_post(self) -> None:
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
            status, _headers, _body = self.call(method, f"{self.base}/api/echo", {"a": 1} if method != "HEAD" else None)
            self.assertEqual(status, 405, method)
        self.assertEqual(self.posted, [])

    def test_a_refusal_becomes_its_status_and_one_line(self) -> None:
        self.fault = Refusal(409, "taken", "taken")
        status, headers, body = self.call("GET", f"{self.base}/raise")
        self.assertEqual(status, 409)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(json.loads(body), {"error": "taken", "code": "taken"})
        self.fault = Refusal(404, "nothing is there")
        status, _headers, body = self.call("GET", f"{self.base}/raise")
        self.assertEqual((status, json.loads(body)), (404, {"error": "nothing is there"}))

    def test_a_fault_is_one_plain_line_without_its_message(self) -> None:
        self.fault = ValueError("secret-1234")
        seen = io.StringIO()
        with contextlib.redirect_stderr(seen):
            status, _headers, body = self.call("GET", f"{self.base}/raise")
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret-1234", body)
        self.assertEqual(json.loads(body), {"error": "the page failed on this machine; see the terminal"})
        # The terminal hears what kind of fault and where, under the page's own name.
        self.assertEqual(seen.getvalue().count("\n"), 1)
        self.assertTrue(seen.getvalue().startswith("shell-test: ValueError at test_localpage.py:"), seen.getvalue())
        self.assertNotIn("secret-1234", seen.getvalue())
        self.assertNotIn(self.page.key, seen.getvalue())

    def test_a_rutor_error_maps_to_a_status(self) -> None:
        cases: list[tuple[RutorError, int]] = [
            (network("x"), 502),
            (environment("x"), 500),
            (refused("x"), 409),
            (bad_input("x"), 400),
        ]
        for fault, expected in cases:
            self.fault = fault
            status, _headers, body = self.call("GET", f"{self.base}/raise")
            self.assertEqual((status, json.loads(body)), (expected, {"error": "x"}))
        # What a service or a child said is one short printable line by the time it leaves.
        self.fault = refused("<script>x</script>\n" * 100)
        _status, _headers, body = self.call("GET", f"{self.base}/raise")
        said = json.loads(body)["error"]
        self.assertLessEqual(len(said), 200)
        self.assertNotIn("\n", said)

    def test_json_reply_is_utf8_json(self) -> None:
        reply = localpage.json_reply(201, {"ord": "å"})
        self.assertEqual((reply.status, reply.content_type), (201, "application/json; charset=utf-8"))
        self.assertEqual(json.loads(reply.body.decode("utf-8")), {"ord": "å"})


if __name__ == "__main__":
    unittest.main()
