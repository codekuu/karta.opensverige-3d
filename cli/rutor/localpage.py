"""The shell around a page that runs on this machine.

`rutor review` and `rutor app` each serve a page that acts with the login of whoever started
it. Such a page is shut to everything but the browser tab it was opened in:

- it listens on 127.0.0.1 only;
- every address starts with a long random key made at start, and nothing answers without it;
- the Host header must be the address that was printed, so another site cannot reach it
  under a name of its own;
- a POST must be JSON, small, and must not come from another page;
- every answer forbids the browser to load anything from outside, to frame the page or to
  keep it;
- a fault leaves as one fixed line. This process holds the login, and no fault text is
  trusted to leave it out.

The shell knows nothing about cells. A page gives it two functions, one for GET and one for
POST, and they see only requests that have passed everything above.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rutor import api
from rutor.errors import RutorError

POLICY = (
    "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
BODY_MAX = 4096

# RutorError codes: 1 bad input, 2 the service refused, 3 this machine, 4 the network.
STATUS_FOR = {1: 400, 2: 409, 3: 500, 4: 502}


@dataclass(frozen=True)
class Reply:
    """One answer: a status, what kind of bytes, and the bytes."""

    status: int
    content_type: str
    body: bytes


def json_reply(status: int, payload: object) -> Reply:
    return Reply(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


class Refusal(Exception):
    """An answer that ends the request: a status and one line for the page."""

    def __init__(self, status: int, message: str, code: str = "") -> None:
        self.status = status
        self.message = message
        # A word the page knows and puts into its own language.
        self.code = code
        super().__init__(message)


# The path after the key, and for a POST the raw body. None means there is nothing there.
Get = Callable[[str], "Reply | None"]
Post = Callable[[str, bytes], "Reply | None"]

_NOT_FOUND = json_reply(404, {"error": "not found"})


def _handler(name: str, get: Get, post: Post, key: str, host: str, body_max: int) -> type[BaseHTTPRequestHandler]:
    origin = f"http://{host}"

    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, format: str, *args: object) -> None:
            # The address holds the key, so requests are not logged.
            return

        def _send(self, reply: Reply) -> None:
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            try:
                self.end_headers()
                self.wfile.write(reply.body)
            except (BrokenPipeError, ConnectionResetError):
                # The browser stopped waiting, as it does when the person moves on.
                pass

        def _json(self, status: int, payload: object) -> None:
            self._send(json_reply(status, payload))

        def _inside(self) -> str | None:
            """The path after the key, or None once the request has been turned away."""
            if (self.headers.get("Host") or "") != host:
                self._json(403, {"error": "this page answers on this machine only"})
                return None
            path = self.path.split("?", 1)[0]
            parts = path.split("/", 2)
            if len(parts) != 3 or parts[0] != "" or not hmac.compare_digest(parts[1].encode(), key.encode()):
                self._send(_NOT_FOUND)
                return None
            return parts[2]

        def _run(self, work: Callable[[], Reply | None]) -> None:
            try:
                reply = work()
            except Refusal as exc:
                self._json(exc.status, {"error": exc.message, "code": exc.code} if exc.code else {"error": exc.message})
            except RutorError as exc:
                self._json(STATUS_FOR.get(exc.code, 500), {"error": api.one_line(exc.message)})
            except Exception as exc:  # noqa: BLE001 - the last stop before the socket
                # The kind of fault and where, for the terminal. Never the message: this
                # process holds the login and no fault text is trusted to leave it out.
                where = traceback.extract_tb(exc.__traceback__)[-1]
                print(f"{name}: {type(exc).__name__} at {Path(where.filename).name}:{where.lineno}", file=sys.stderr)
                self._json(500, {"error": "the page failed on this machine; see the terminal"})
            else:
                self._send(reply if reply is not None else _NOT_FOUND)

        def do_GET(self) -> None:
            rest = self._inside()
            if rest is None:
                return
            self._run(lambda: get(rest))

        def do_POST(self) -> None:
            rest = self._inside()
            if rest is None:
                return
            sent_from = self.headers.get("Origin")
            if sent_from is not None and sent_from != origin:
                self._json(403, {"error": "this page takes no requests from other pages"})
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self._json(415, {"error": "send JSON"})
                return
            length = self.headers.get("Content-Length") or ""
            if not (length.isascii() and length.isdigit()) or not 0 < int(length) <= body_max:
                self._json(400, {"error": "the body is missing or too large"})
                return
            raw = self.rfile.read(int(length))
            self._run(lambda: post(rest, raw))

        def _no(self) -> None:
            self._json(405, {"error": "GET and POST only"})

        do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no

    return Handler


class _OwnPort(ThreadingHTTPServer):
    # Nobody else may listen on the port while the page is up. On Windows the default lets them.
    allow_reuse_address = False
    # The word in front of a fault line on stderr.
    page_name = "page"

    def handle_error(self, request: object, client_address: object) -> None:
        # A browser drops a connection whenever the person moves on. That is not a fault,
        # and the standard report of it would print the address, which holds the key.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        print(f"{self.page_name}: {type(sys.exc_info()[1]).__name__} while answering", file=sys.stderr)


class LocalPage:
    """A page on 127.0.0.1, under an address nobody can guess.

    The port is a free one, new at every start. A fixed port would give the page the same
    origin every time, and whatever an earlier program left in the browser under that origin
    would still be there.
    """

    def __init__(self, name: str, get: Get, post: Post, body_max: int = BODY_MAX) -> None:
        self.key = secrets.token_urlsafe(32)
        self._httpd = _OwnPort(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self._httpd.page_name = name
        self.port = int(self._httpd.server_address[1])
        self._httpd.RequestHandlerClass = _handler(name, get, post, self.key, f"127.0.0.1:{self.port}", body_max)
        self.url = f"http://127.0.0.1:{self.port}/{self.key}/"
        self._thread: threading.Thread | None = None
        # Set while serve_forever runs on the caller's own thread.
        self._serving = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._serving.set()
        try:
            self._httpd.serve_forever()
        finally:
            self._serving.clear()

    def end_serving(self) -> None:
        """Make serve_forever return. For another thread than the one that is inside it."""
        if self._serving.is_set():
            # shutdown() waits for the loop to leave, and would wait for ever on a loop
            # that never started.
            self._httpd.shutdown()

    def stop(self) -> None:
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._httpd.server_close()
