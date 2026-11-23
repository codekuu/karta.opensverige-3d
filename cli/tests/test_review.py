"""The review page: the maintainer's local page for approving and rejecting uploaded cells."""

from __future__ import annotations

import http.client
import json
import os
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rutor import config, review
from rutor.config import write_token
from rutor.errors import RutorError
from tests.fake_service import FakeService
from tests.png import _chunk, minimal_png

GOTEBORG_DZI = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" Format="webp" Overlap="0" TileSize="512">\n'
    b'<Size Width="133120" Height="120832"/>\n</Image>\n'
)
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 " + b"\x00" * 18
TOKEN = "review-test-token"


class FakeTiles:
    """Stands in for the tile service: a DZI per map and the tiles put in `files`."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"/v1/goteborg-norr.dzi": GOTEBORG_DZI}
        self.requests: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                outer.requests.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
                body = outer.files.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _png(colour: int = 2, depth: int = 8, extra: tuple[tuple[bytes, bytes], ...] = (), interlace: int = 0) -> bytes:
    """A 1536 px PNG as the service lets through: any colour type, any chunks with a good CRC."""
    ihdr = struct.pack(">IIBBBBB", 1536, 1536, depth, colour, 0, 0, interlace)
    chunks = [_chunk(b"IHDR", ihdr), *(_chunk(tag, data) for tag, data in extra), _chunk(b"IDAT", b"\x00"), _chunk(b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _waiting(col: int, row: int, **extra: object) -> dict[str, object]:
    return {
        "sheet": "goteborg-norr",
        "col": col,
        "row": row,
        "state": "vantar",
        "owner_login": "klang",
        "uploaded_at": "2026-11-23T02:10:00Z",
        "bytes": 2_400_000,
        **extra,
    }


class MapGeometryTests(unittest.TestCase):
    def test_a_cell_is_one_tile_on_the_level_below_full_size(self) -> None:
        dzi = review.parse_dzi(GOTEBORG_DZI)
        assert dzi is not None
        self.assertEqual((dzi.width, dzi.height), (133120, 120832))
        self.assertEqual(review.cell_tile(dzi, 64, 58), (17, 64, 58))

    def test_a_cell_outside_the_picture_has_no_tile(self) -> None:
        dzi = review.parse_dzi(GOTEBORG_DZI)
        assert dzi is not None
        self.assertIsNone(review.cell_tile(dzi, -1, 0))
        self.assertIsNone(review.cell_tile(dzi, 0, -1))
        self.assertEqual(review.cell_tile(dzi, 129, 117), (17, 129, 117))
        self.assertIsNone(review.cell_tile(dzi, 130, 0))
        self.assertIsNone(review.cell_tile(dzi, 0, 118))

    def test_a_map_laid_out_another_way_is_not_guessed_at(self) -> None:
        self.assertIsNone(review.parse_dzi(GOTEBORG_DZI.replace(b'Overlap="0"', b'Overlap="1"')))
        self.assertIsNone(review.parse_dzi(GOTEBORG_DZI.replace(b'TileSize="512"', b'TileSize="256"')))
        self.assertIsNone(review.parse_dzi(GOTEBORG_DZI.replace(b'Format="webp"', b'Format="png"')))
        self.assertIsNone(review.parse_dzi(b"<html>Not for this site</html>"))
        self.assertIsNone(review.parse_dzi(GOTEBORG_DZI.replace(b"133120", b"0")))

    def test_where_the_upload_lies_among_its_neighbours(self) -> None:
        # The view is three cells wide. A 1536 px upload is the cell plus 256 px on each side.
        # The last number is how much of the upload, on each side, lies outside its own cell.
        start, size, edge = review.overlay_box(1536)
        self.assertEqual((start, size), (0.25, 0.5))
        self.assertAlmostEqual(edge, 256 / 1536)
        start, size, edge = review.overlay_box(1024)
        self.assertAlmostEqual(start, 1 / 3)
        self.assertAlmostEqual(size, 1 / 3)
        self.assertEqual(edge, 0)


class TilesUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("PIXELBYGGET_TILES_URL")

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("PIXELBYGGET_TILES_URL", None)
        else:
            os.environ["PIXELBYGGET_TILES_URL"] = self._saved

    def test_the_tile_address_is_the_projects_own_or_this_machine(self) -> None:
        os.environ.pop("PIXELBYGGET_TILES_URL", None)
        self.assertEqual(config.tiles_url(), "https://pixelkarta.gustaf-garnow.workers.dev/v1")
        os.environ["PIXELBYGGET_TILES_URL"] = "http://127.0.0.1:8788/v1/"
        self.assertEqual(config.tiles_url(), "http://127.0.0.1:8788/v1")
        for bad in ("https://evil.example/v1", "http://127.0.0.1.evil.example/v1", "file:///etc/passwd"):
            os.environ["PIXELBYGGET_TILES_URL"] = bad
            with self.assertRaises(RutorError):
                config.tiles_url()


class ReviewServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeService()
        self.tiles = FakeTiles()
        names = ("PIXELBYGGET_HOME", "PIXELBYGGET_URL", "PIXELBYGGET_TILES_URL")
        self._saved = {name: os.environ.get(name) for name in names}
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ["PIXELBYGGET_URL"] = self.svc.start()
        os.environ["PIXELBYGGET_TILES_URL"] = self.tiles.url
        write_token(TOKEN)
        self.server = review.ReviewServer(TOKEN)
        self.server.start()
        self.port = self.server.port
        self.base = f"/{self.server.key}"

    def tearDown(self) -> None:
        self.server.stop()
        self.tiles.stop()
        self.svc.stop()
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

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

    def service_calls(self, method: str, path: str) -> list[dict[str, object]]:
        return [r for r in self.svc.requests if r.get("method") == method and str(r.get("path")).startswith(path)]

    # ---- who gets in

    def test_the_address_carries_a_long_random_key(self) -> None:
        self.assertEqual(self.server.url, f"http://127.0.0.1:{self.port}/{self.server.key}/")
        self.assertGreaterEqual(len(self.server.key), 32)
        other = review.ReviewServer(TOKEN)
        other.stop()
        self.assertNotEqual(self.server.key, other.key)

    def test_nothing_answers_without_the_key(self) -> None:
        self.svc.state["queue"] = [_waiting(0, 1)]
        _status, _headers, body = self.call("GET", f"{self.base}/api/queue")
        self.assertIn(b"klang", body)
        for path in ("/", "/api/queue", "/wrong-key/", "/wrong-key/api/queue", f"{self.base}x/api/queue"):
            status, _headers, body = self.call("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"klang", body)
        status, _headers, _body = self.call("POST", "/wrong-key/api/approve", {"sheet": "goteborg-norr", "col": 0, "row": 1})
        self.assertEqual(status, 404)
        self.assertEqual(self.service_calls("POST", "/v1/approve"), [])

    def test_a_request_under_another_host_name_is_refused(self) -> None:
        # A page elsewhere can point its own name at 127.0.0.1. It cannot fake the Host header.
        status, _headers, _body = self.call("GET", f"{self.base}/api/queue", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        # Only the address that was printed. The name localhost can also mean ::1, where
        # this server does not listen and another program might.
        status, _headers, _body = self.call("GET", f"{self.base}/api/queue", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.call("GET", f"{self.base}/api/queue", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        cell = {"sheet": "goteborg-norr", "col": 0, "row": 1}
        status, _headers, _body = self.call("POST", f"{self.base}/api/approve", cell, {"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        self.assertEqual(self.service_calls("POST", "/v1/approve"), [])

    def test_a_post_from_another_page_is_refused(self) -> None:
        cell = {"sheet": "goteborg-norr", "col": 0, "row": 1}
        for origin in ("https://evil.example", "null", f"http://localhost:{self.port}", f"https://127.0.0.1:{self.port}"):
            status, _headers, _body = self.call("POST", f"{self.base}/api/approve", cell, {"Origin": origin})
            self.assertEqual(status, 403, origin)
        status, _headers, _body = self.call(
            "POST", f"{self.base}/api/approve", json.dumps(cell).encode(), {"Content-Type": "text/plain"}
        )
        self.assertEqual(status, 415)
        self.assertEqual(self.service_calls("POST", "/v1/approve"), [])
        status, _headers, _body = self.call(
            "POST", f"{self.base}/api/approve", cell, {"Origin": f"http://127.0.0.1:{self.port}"}
        )
        self.assertEqual(status, 200)

    def test_every_answer_locks_the_page_down(self) -> None:
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": _png()}
        self.tiles.files["/v1/goteborg-norr_files/17/64_58.webp"] = WEBP
        cell = {"sheet": "goteborg-norr", "col": 0, "row": 1}
        asked: list[tuple[str, str, object | None, int]] = [
            ("GET", f"{self.base}/", None, 200),
            ("GET", f"{self.base}/app.js", None, 200),
            ("GET", f"{self.base}/app.css", None, 200),
            ("GET", f"{self.base}/api/queue", None, 200),
            ("GET", f"{self.base}/img/cell/goteborg-norr/0_1.png", None, 200),
            ("GET", f"{self.base}/img/map/goteborg-norr/64_58.webp", None, 200),
            ("GET", "/nope", None, 404),
            ("POST", f"{self.base}/api/approve", cell, 200),
            ("POST", f"{self.base}/api/reject", cell, 400),
            ("PUT", f"{self.base}/api/approve", cell, 405),
        ]
        for method, path, body, expected in asked:
            status, headers, _body = self.call(method, path, body)
            self.assertEqual(status, expected, path)
            policy = headers.get("content-security-policy", "")
            self.assertIn("default-src 'none'", policy, path)
            self.assertIn("frame-ancestors 'none'", policy, path)
            self.assertNotIn("unsafe", policy, path)
            self.assertEqual(headers.get("referrer-policy"), "no-referrer", path)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", path)
            self.assertEqual(headers.get("cache-control"), "no-store", path)
            self.assertNotIn("access-control-allow-origin", headers, path)
            del status

    def test_the_page_and_its_files_are_served(self) -> None:
        status, headers, body = self.call("GET", f"{self.base}/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn(b"Granskning", body)
        self.assertIn(b'src="app.js"', body)
        # No inline script or style: the policy would stop them, and the page must not need them.
        self.assertNotIn(b"<style", body)
        self.assertNotIn(b"style=", body)
        self.assertNotIn(b"onclick", body)
        status, headers, _body = self.call("GET", f"{self.base}/app.js")
        self.assertEqual((status, headers["content-type"]), (200, "text/javascript; charset=utf-8"))
        status, headers, _body = self.call("GET", f"{self.base}/app.css")
        self.assertEqual((status, headers["content-type"]), (200, "text/css; charset=utf-8"))
        status, _headers, _body = self.call("GET", f"{self.base}/../config.py")
        self.assertEqual(status, 404)
        status, _headers, _body = self.call("GET", f"{self.base}/review.py")
        self.assertEqual(status, 404)

    def test_the_login_never_leaves_in_an_answer(self) -> None:
        self.svc.state["queue"] = [_waiting(0, 1)]
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": _png(), "goteborg-norr/0_1.json": b"{}"}
        paths = ("/", "/app.js", "/api/queue", "/api/cell/goteborg-norr/0_1", "/img/cell/goteborg-norr/0_1.png")
        for path in paths:
            _status, headers, body = self.call("GET", self.base + path)
            self.assertNotIn(TOKEN.encode(), body, path)
            self.assertNotIn(TOKEN, json.dumps(headers), path)

    # ---- the queue

    def test_the_queue_lists_waiting_and_approved_cells(self) -> None:
        self.svc.state["queue"] = [
            _waiting(0, 1),
            _waiting(2, 2, state="godkand", owner_login="other"),
        ]
        status, headers, body = self.call("GET", f"{self.base}/api/queue")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(
            json.loads(body),
            {
                "waiting": [
                    {
                        "sheet": "goteborg-norr",
                        "col": 0,
                        "row": 1,
                        "login": "klang",
                        "uploaded_at": "2026-11-23T02:10:00Z",
                        "bytes": 2_400_000,
                    }
                ],
                "approved": [
                    {
                        "sheet": "goteborg-norr",
                        "col": 2,
                        "row": 2,
                        "login": "other",
                        "uploaded_at": "2026-11-23T02:10:00Z",
                        "bytes": 2_400_000,
                    }
                ],
            },
        )
        for call in self.service_calls("GET", "/v1/queue"):
            headers_sent = call["headers"]
            assert isinstance(headers_sent, dict)
            self.assertEqual(headers_sent.get("Authorization"), f"Bearer {TOKEN}")

    def test_what_the_service_says_about_a_cell_is_read_field_by_field(self) -> None:
        self.svc.state["queue"] = [
            _waiting(0, 1, owner_login="<img src=x onerror=alert(1)>", uploaded_at="yesterday<script>", bytes="many"),
            _waiting(3, 4, sheet="../../etc"),
            {**_waiting(5, 6), "col": "5"},
            {**_waiting(7, 8), "row": -1},
            "not a cell",
        ]
        status, _headers, body = self.call("GET", f"{self.base}/api/queue")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body)["waiting"],
            [{"sheet": "goteborg-norr", "col": 0, "row": 1, "login": "", "uploaded_at": "", "bytes": 0}],
        )

    def test_a_refusal_from_the_service_is_passed_on_as_one_line(self) -> None:
        self.svc.fail_next_with(403, b'{"error":"not_admin","message":"Only the admin\\nmay do that."}', "application/json")
        status, _headers, body = self.call("GET", f"{self.base}/api/queue")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "refused: not_admin: Only the admin may do that."})

    # ---- one cell

    def test_the_facts_of_a_cell_come_from_its_manifest(self) -> None:
        manifest = {
            "pipeline": "2026.11-a",
            "seed": 7,
            "sha256": "ab" * 32,
            "width": 1536,
            "height": 1536,
            "source": {"provider": "open", "version": "osm 2026-11-01; ortho 2023; terrain hojdmodell_2022"},
            "gpu": "NVIDIA GeForce RTX 5070",
            "renderedAt": "2026-11-23T02:08:11Z",
            "check": "ok",
            "login": "klang",
            "github_id": 4242,
        }
        self.svc.state["inbox"] = {"goteborg-norr/64_58.json": json.dumps(manifest).encode()}
        status, _headers, body = self.call("GET", f"{self.base}/api/cell/goteborg-norr/64_58")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "check": "ok",
                "provider": "open",
                "version": "osm 2026-11-01; ortho 2023; terrain hojdmodell_2022",
                "gpu": "NVIDIA GeForce RTX 5070",
                "rendered_at": "2026-11-23T02:08:11Z",
                "pipeline": "2026.11-a",
                "side": 1536,
                "box": list(review.overlay_box(1536)),
                "map": True,
            },
        )

    def test_a_manifest_with_odd_content_gives_plain_short_text(self) -> None:
        manifest = {"check": "ok\n<b>", "gpu": "x" * 500, "source": "nope", "width": 99999, "renderedAt": 12}
        self.svc.state["inbox"] = {"goteborg-norr/64_58.json": json.dumps(manifest).encode()}
        status, _headers, body = self.call("GET", f"{self.base}/api/cell/goteborg-norr/64_58")
        self.assertEqual(status, 200)
        facts = json.loads(body)
        self.assertEqual(facts["check"], "ok <b>")
        self.assertLessEqual(len(facts["gpu"]), 200)
        self.assertEqual((facts["provider"], facts["version"], facts["rendered_at"]), ("", "", ""))
        self.assertEqual((facts["side"], facts["box"]), (0, None))

    def test_a_city_without_a_map_is_said_so(self) -> None:
        self.svc.state["inbox"] = {"eslov/8_6.json": b'{"check":"ok","width":1536}'}
        status, _headers, body = self.call("GET", f"{self.base}/api/cell/eslov/8_6")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["map"], False)

    def test_a_cell_address_that_is_not_one_finds_nothing(self) -> None:
        for path in ("/api/cell/GOTEBORG/0_1", "/api/cell/goteborg-norr/0_x", "/api/cell/goteborg-norr/0_1/extra", "/img/cell/goteborg-norr/00000_1.png", "/img/map/goteborg-norr/0_1.png"):
            status, _headers, _body = self.call("GET", self.base + path)
            self.assertEqual(status, 404, path)
        self.assertEqual(self.service_calls("GET", "/v1/inkorg"), [])

    # ---- pictures

    def test_the_uploaded_picture_is_passed_on_as_a_png(self) -> None:
        png = _png(extra=((b"tEXt", b"Software\x00rutor"), (b"pHYs", struct.pack(">IIB", 2835, 2835, 1))))
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": png}
        status, headers, body = self.call("GET", f"{self.base}/img/cell/goteborg-norr/0_1.png")
        self.assertEqual((status, headers["content-type"]), (200, "image/png"))
        self.assertEqual(body, png)

    def test_an_upload_that_is_not_a_png_is_not_shown(self) -> None:
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": b"<svg onload=alert(1)>"}
        status, headers, body = self.call("GET", f"{self.base}/img/cell/goteborg-norr/0_1.png")
        self.assertEqual(status, 502)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertNotIn(b"<svg", body)

    def test_only_a_plain_picture_is_shown(self) -> None:
        # A browser plays an animation, lets the map show through alpha and applies a gamma.
        # The program that lays the cell into the map may do none of that, so the reviewer
        # would approve one picture and publish another.
        odd: dict[str, bytes] = {
            "animated": _png(extra=((b"acTL", struct.pack(">II", 2, 0)),)),
            "see-through": _png(colour=6),
            "grey with alpha": _png(colour=4),
            "transparent colour": _png(extra=((b"tRNS", b"\x00" * 6),)),
            "gamma": _png(extra=((b"gAMA", struct.pack(">I", 1)),)),
            "colour profile": _png(extra=((b"iCCP", b"x\x00\x00x"),)),
            "palette": _png(colour=3, extra=((b"PLTE", b"\x00" * 3),)),
            "sixteen bit": _png(depth=16),
            "interlaced": _png(interlace=1),
            "unknown chunk": _png(extra=((b"prIv", b"x"),)),
            "cut short": _png()[:-6],
            "trailing bytes": _png() + b"<script>",
            "data before the header": b"\x89PNG\r\n\x1a\n" + _chunk(b"IDAT", b"\x00") + _chunk(b"IEND", b""),
        }
        for name, data in odd.items():
            self.svc.state["inbox"] = {"goteborg-norr/0_1.png": data}
            status, _headers, body = self.call("GET", f"{self.base}/img/cell/goteborg-norr/0_1.png")
            self.assertEqual(status, 502, name)
            answer = json.loads(body)
            self.assertEqual(answer["code"], "not_plain", name)
            self.assertIn("plain picture", answer["error"], name)

    def test_an_upload_larger_than_the_service_takes_is_not_read(self) -> None:
        self.svc.state["inbox"] = {"goteborg-norr/0_1.png": _png() + b"\x00" * (9 * 1024 * 1024)}
        status, _headers, _body = self.call("GET", f"{self.base}/img/cell/goteborg-norr/0_1.png")
        self.assertEqual(status, 502)

    def test_the_map_is_asked_about_as_this_machine_and_without_the_login(self) -> None:
        self.call("GET", f"{self.base}/img/map/goteborg-norr/64_58.webp")
        asked = [r for r in self.tiles.requests if r["path"].endswith(".dzi")]
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0].get("referer"), "http://127.0.0.1/")
        self.assertNotIn("authorization", asked[0])

    def test_a_cell_can_be_judged_while_the_tile_service_is_down(self) -> None:
        self.svc.state["inbox"] = {"goteborg-norr/64_58.json": b'{"check":"ok","width":1536}'}
        os.environ["PIXELBYGGET_TILES_URL"] = "http://127.0.0.1:9/v1"
        status, _headers, body = self.call("GET", f"{self.base}/api/cell/goteborg-norr/64_58")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["map"], False)
        status, _headers, _body = self.call("GET", f"{self.base}/img/map/goteborg-norr/64_58.webp")
        self.assertEqual(status, 502)

    def test_a_map_tile_is_fetched_as_this_machine_and_without_the_login(self) -> None:
        self.tiles.files["/v1/goteborg-norr_files/17/64_58.webp"] = WEBP
        status, headers, body = self.call("GET", f"{self.base}/img/map/goteborg-norr/64_58.webp")
        self.assertEqual((status, headers["content-type"]), (200, "image/webp"))
        self.assertEqual(body, WEBP)
        tile_calls = [r for r in self.tiles.requests if r["path"].endswith("64_58.webp")]
        self.assertEqual(len(tile_calls), 1)
        # The sender says what it is, a page on this machine, and nothing more: no port, no key.
        self.assertEqual(tile_calls[0].get("referer"), "http://127.0.0.1/")
        self.assertNotIn(self.server.key, json.dumps(tile_calls[0]))
        self.assertNotIn("authorization", tile_calls[0])
        # The tiles never change, so a second look does not ask again.
        self.call("GET", f"{self.base}/img/map/goteborg-norr/64_58.webp")
        self.assertEqual(len([r for r in self.tiles.requests if r["path"].endswith("64_58.webp")]), 1)

    def test_no_tile_outside_the_map_or_of_another_kind(self) -> None:
        status, _headers, _body = self.call("GET", f"{self.base}/img/map/goteborg-norr/130_0.webp")
        self.assertEqual(status, 404)
        self.assertEqual([r for r in self.tiles.requests if "_files/" in r["path"]], [])
        self.tiles.files["/v1/goteborg-norr_files/17/1_1.webp"] = b"<html>Not for this site</html>"
        status, _headers, body = self.call("GET", f"{self.base}/img/map/goteborg-norr/1_1.webp")
        self.assertEqual(status, 502)
        self.assertNotIn(b"<html", body)
        status, _headers, _body = self.call("GET", f"{self.base}/img/map/eslov/1_1.webp")
        self.assertEqual(status, 404)

    # ---- the two buttons

    def test_approve_goes_to_the_service_with_the_login(self) -> None:
        status, _headers, body = self.call("POST", f"{self.base}/api/approve", {"sheet": "goteborg-norr", "col": 0, "row": 1})
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))
        calls = self.service_calls("POST", "/v1/approve")
        self.assertEqual(len(calls), 1)
        sent = calls[0]["body"]
        assert isinstance(sent, bytes)
        self.assertEqual(json.loads(sent), {"sheet": "goteborg-norr", "col": 0, "row": 1})
        headers_sent = calls[0]["headers"]
        assert isinstance(headers_sent, dict)
        self.assertEqual(headers_sent.get("Authorization"), f"Bearer {TOKEN}")

    def test_reject_needs_a_reason(self) -> None:
        cell = {"sheet": "goteborg-norr", "col": 0, "row": 1}
        for comment in ("", "   ", "x" * 501, 7):
            status, _headers, _body = self.call("POST", f"{self.base}/api/reject", {**cell, "comment": comment})
            self.assertEqual(status, 400, repr(comment))
        self.assertEqual(self.service_calls("POST", "/v1/reject"), [])
        status, _headers, _body = self.call("POST", f"{self.base}/api/reject", {**cell, "comment": " taken flyter ihop "})
        self.assertEqual(status, 200)
        sent = self.service_calls("POST", "/v1/reject")[0]["body"]
        assert isinstance(sent, bytes)
        self.assertEqual(json.loads(sent), {**cell, "comment": "taken flyter ihop"})

    def test_a_body_that_names_no_cell_changes_nothing(self) -> None:
        bodies: list[object] = [
            {"sheet": "goteborg-norr", "col": "0", "row": 1},
            {"sheet": "../x", "col": 0, "row": 1},
            {"sheet": "goteborg-norr", "col": True, "row": 1},
            ["goteborg-norr", 0, 1],
            b"{not json",
            # A real cell in a body larger than any the page sends.
            json.dumps({"sheet": "goteborg-norr", "col": 0, "row": 1, "pad": "x" * 5000}).encode(),
        ]
        for body in bodies:
            status, _headers, _answer = self.call("POST", f"{self.base}/api/approve", body)
            self.assertEqual(status, 400, repr(body)[:40])
        self.assertEqual(self.service_calls("POST", "/v1/approve"), [])

    def test_a_cell_someone_else_already_judged_says_so(self) -> None:
        self.svc.fail_next_with(409, b'{"error":"not_waiting","message":"That cell is not waiting for review."}', "application/json")
        status, _headers, body = self.call("POST", f"{self.base}/api/approve", {"sheet": "goteborg-norr", "col": 0, "row": 1})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "refused: not_waiting: That cell is not waiting for review."})

    def test_only_get_and_post(self) -> None:
        for method in ("PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"):
            status, headers, _body = self.call(method, f"{self.base}/api/approve")
            self.assertEqual(status, 405, method)
            self.assertNotIn("access-control-allow-origin", headers)

    def test_a_fault_inside_the_page_is_one_plain_line(self) -> None:
        with patch.object(review._Review, "queue", side_effect=ValueError(f"boom {TOKEN}")):
            status, headers, body = self.call("GET", f"{self.base}/api/queue")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"error": "the page failed on this machine; see the terminal"})
        self.assertIn("default-src 'none'", headers.get("content-security-policy", ""))
        status, _headers, _body = self.call("GET", f"{self.base}/api/queue")
        self.assertEqual(status, 200)

    def test_a_length_or_a_body_made_to_trip_the_reader_is_refused(self) -> None:
        cell = json.dumps({"sheet": "goteborg-norr", "col": 0, "row": 1}).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.putrequest("POST", f"{self.base}/api/approve")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "\u00b2".encode("latin-1"))
            connection.endheaders()
            connection.send(cell)
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()
        status, _headers, _body = self.call("POST", f"{self.base}/api/approve", b"[" * 4000)
        self.assertEqual(status, 400)
        self.assertEqual(self.service_calls("POST", "/v1/approve"), [])


if __name__ == "__main__":
    unittest.main()
