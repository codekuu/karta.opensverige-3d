"""rutor review: the maintainer's page for approving and rejecting uploaded cells.

The page runs on this machine only and uses the login `rutor login` saved. It shows each
waiting cell laid into today's map among its neighbours, beside the map as it is, with two
buttons. Nothing here decides anything: the service still checks that the login is the admin's.

The page can approve and reject, so it is shut to everything but the browser tab it was opened
in:

- it listens on 127.0.0.1 only;
- every address starts with a long random key made at start, and nothing answers without it;
- the Host header must be the address that was printed, so another site cannot reach it
  under a name of its own;
- a POST must be JSON and must not come from another page;
- the page loads nothing from outside, and what the service says is shown as text;
- an upload is shown only when it is a plain picture, so that what the reviewer sees is what
  every other program reads out of the same file.

The login stays in this process. It goes to the service and nowhere else: not to the page and
not to the tile service.
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import struct
import sys
import threading
import time
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rutor import api, config
from rutor.errors import RutorError, bad_input, environment, network

PAGE_DIR = Path(__file__).resolve().parent / "review_page"
# The page's own files, by the name the browser asks for. Nothing else is read from disk.
PAGE_FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
}
POLICY = (
    "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

CELL_PX = 1024
TILE_PX = 512
SIDES = (1024, 1536)
MAX_CELL_INDEX = 9999
COMMENT_MAX = 500
BODY_MAX = 4096
DZI_MAX = 4096
MANIFEST_MAX = 64 * 1024
# The service takes no upload over 8 MiB.
UPLOAD_MAX = 8 * 1024 * 1024
# A real tile is a little over 100 kB.
TILE_MAX = 1024 * 1024
TILES_KEPT = 200
NO_MAP_SECONDS = 60.0
# The tile service's list of editions: which sheets have had cells put into them, and the number
# of the edition that is live. It is small, and it is read again this often, so that a cell
# published while the page is open shows among its neighbours.
EDITIONS_MAX = 4 * 1024 * 1024
EDITION_SECONDS = 60.0
EDITION_LARGEST = 999_999

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?")
CELL = r"(?P<sheet>[a-z0-9][a-z0-9-]{0,63})/(?P<col>\d{1,4})_(?P<row>\d{1,4})"
ROUTES = {
    "facts": re.compile(rf"api/cell/{CELL}"),
    "upload": re.compile(rf"img/cell/{CELL}\.png"),
    "tile": re.compile(rf"img/map/{CELL}\.webp"),
}


# ---- where a cell lies in the map picture


@dataclass(frozen=True)
class Dzi:
    """A map picture cut the way the pixel maps are: 512 px WebP tiles that do not overlap."""

    width: int
    height: int


def _attribute(raw: bytes, name: bytes) -> bytes | None:
    found = re.search(rb"\b" + name + rb'="([A-Za-z0-9]{1,12})"', raw)
    return found.group(1) if found else None


def parse_dzi(raw: bytes) -> Dzi | None:
    """The map's size, or None when the file is not a map cut the way this page counts on."""
    if len(raw) > DZI_MAX or b"<Image" not in raw:
        return None
    if _attribute(raw, b"Format") != b"webp" or _attribute(raw, b"Overlap") != b"0":
        return None
    if _attribute(raw, b"TileSize") != str(TILE_PX).encode():
        return None
    width, height = _attribute(raw, b"Width"), _attribute(raw, b"Height")
    if width is None or height is None or not width.isdigit() or not height.isdigit():
        return None
    if not (0 < int(width) <= 1 << 24 and 0 < int(height) <= 1 << 24):
        return None
    return Dzi(int(width), int(height))


def edition_of(raw: bytes, sheet: str) -> int:
    """The live edition of a sheet in the tile service's editions file, or 0 for the base map.

    A sheet's tiles never change under their address. When a volunteer's cell is put into the
    map the sheet gets a new edition, and its tiles are asked for as <sheet>@<edition>_files/...
    Anything the file says that is not a plain positive number means the base.
    """
    try:
        sheets = json.loads(raw.decode("utf-8")).get("sheets")
        rev = sheets.get(sheet).get("rev")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return 0
    return rev if isinstance(rev, int) and not isinstance(rev, bool) and 0 < rev <= EDITION_LARGEST else 0


def cell_tile(dzi: Dzi, col: int, row: int) -> tuple[int, int, int] | None:
    """The one tile that shows a whole cell: (level, x, y), or None outside the picture.

    A cell is 1024 px of the full picture and a tile is 512 px, so on the level below full
    size a tile covers exactly one cell and has the cell's own column and row.
    """
    full = (max(dzi.width, dzi.height) - 1).bit_length()
    if full < 1 or col < 0 or row < 0:
        return None
    if col * CELL_PX >= dzi.width or row * CELL_PX >= dzi.height:
        return None
    return full - 1, col, row


def overlay_box(side: int) -> tuple[float, float, float]:
    """Where an upload lies in a view three cells wide: (start, size, edge), as fractions.

    start and size are fractions of the view. edge is the part of the upload, on each side,
    that lies outside its own cell: a 1536 px upload is the cell plus 256 px all round.
    """
    margin = (side - CELL_PX) / 2
    return (CELL_PX - margin) / (3 * CELL_PX), side / (3 * CELL_PX), margin / side


# ---- one picture for every reader

# The redraw writes 8-bit RGB with nothing but the picture in the file. These chunks change no
# pixel: the size of a pixel in print, text, a time, and "this is sRGB", which is what a file
# that says nothing is taken to be.
PLAIN_CHUNKS = frozenset({b"IHDR", b"IDAT", b"IEND", b"pHYs", b"tEXt", b"zTXt", b"iTXt", b"tIME", b"sRGB"})


def not_plain(data: bytes) -> str | None:
    """Why this PNG may look different to two programs, or None when it is a plain picture.

    A browser plays an animation, lets what is behind show through alpha, and applies a gamma
    or a colour profile. A program that only reads the pixels may do none of it. An upload
    with any of that could be approved as one picture and laid into the map as another.
    """
    if not data.startswith(PNG_SIGNATURE):
        return "it is not a PNG"
    at = len(PNG_SIGNATURE)
    seen: list[bytes] = []
    while at + 12 <= len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        tag = data[at + 4 : at + 8]
        if length > len(data) - at - 12:
            return "it is cut short"
        if not seen:
            if tag != b"IHDR" or length != 13:
                return "it does not start with its header"
            depth, colour, _compression, _filter, interlace = struct.unpack(">BBBBB", data[at + 16 : at + 21])
            if (depth, colour, interlace) != (8, 2, 0):
                return "it is not 8-bit RGB without alpha"
        elif tag not in PLAIN_CHUNKS or tag == b"IHDR":
            return f"it holds a {api.one_line(tag.decode('latin-1'), 4)!r} part"
        seen.append(tag)
        at += 12 + length
        if tag == b"IEND":
            return None if at == len(data) and b"IDAT" in seen else "something follows the picture"
    return "it is cut short"


# ---- what the service says, read field by field


def _text(value: object, limit: int) -> str:
    return api.one_line(value, limit) if isinstance(value, str) else ""


def _index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CELL_INDEX:
        return None
    return value


def _queue_entry(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    sheet, col, row = raw.get("sheet"), _index(raw.get("col")), _index(raw.get("row"))
    if not isinstance(sheet, str) or not config.SHEET_ID.fullmatch(sheet) or col is None or row is None:
        return None
    login, uploaded, size = raw.get("owner_login"), raw.get("uploaded_at"), raw.get("bytes")
    return {
        "sheet": sheet,
        "col": col,
        "row": row,
        "login": login if isinstance(login, str) and GITHUB_LOGIN.fullmatch(login) else "",
        "uploaded_at": uploaded if isinstance(uploaded, str) and ISO_TIME.fullmatch(uploaded) else "",
        "bytes": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
    }


def _cell_body(raw: bytes) -> tuple[dict[str, object], dict[str, object]]:
    """The cell a POST names, and the whole body. Raises bad_input for anything else."""
    try:
        body = json.loads(raw.decode("utf-8"))
    # A body of nothing but brackets runs the reader out of depth on some Pythons.
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise bad_input("the body is not JSON") from exc
    if not isinstance(body, dict):
        raise bad_input("the body names no cell")
    sheet, col, row = body.get("sheet"), _index(body.get("col")), _index(body.get("row"))
    if not isinstance(sheet, str) or not config.SHEET_ID.fullmatch(sheet) or col is None or row is None:
        raise bad_input("the body names no cell")
    return {"sheet": sheet, "col": col, "row": row}, body


class _Answer(Exception):
    """An answer that ends the request: a status and one line for the page."""

    def __init__(self, status: int, message: str, code: str = "") -> None:
        self.status = status
        self.message = message
        # A word the page knows and puts into its own language.
        self.code = code
        super().__init__(message)


# The tile service answers only pages it knows, told by the Referer header, and it knows pages
# on this machine. So the sender says what it is: a page on 127.0.0.1. No port and no path, so
# the key in this page's address never travels.
_FROM_THIS_MACHINE = {"Referer": "http://127.0.0.1/"}


# RutorError codes: 1 bad input, 2 the service refused, 3 this machine, 4 the network.
STATUS_FOR = {1: 400, 2: 409, 3: 500, 4: 502}


class _Review:
    """What the page can ask for. Holds the login; no method returns it."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._lock = threading.Lock()
        self._maps: dict[str, tuple[Dzi | None, float]] = {}
        self._editions: dict[str, tuple[int, float]] = {}
        self._tiles: dict[tuple[str, int, int, int], bytes] = {}

    def queue(self) -> dict[str, object]:
        lists: dict[str, object] = {}
        for name, state in (("waiting", "vantar"), ("approved", "godkand")):
            _status, body = api.get(f"/v1/queue?state={state}", token=self._token)
            cells = body.get("cells") if isinstance(body, dict) else None
            if not isinstance(cells, list):
                raise network("service answer has no cells")
            lists[name] = [entry for entry in map(_queue_entry, cells) if entry is not None]
        return lists

    def facts(self, sheet: str, col: int, row: int) -> dict[str, object]:
        raw = api.download(f"/v1/inkorg/{sheet}/{col}_{row}.json", self._token, MANIFEST_MAX)
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            manifest = None
        if not isinstance(manifest, dict):
            manifest = {}
        source = manifest.get("source")
        if not isinstance(source, dict):
            source = {}
        rendered = manifest.get("renderedAt")
        width = manifest.get("width")
        side = width if isinstance(width, int) and not isinstance(width, bool) and width in SIDES else 0
        try:
            dzi = self._map(sheet)
        except RutorError:
            # Without the tile service the cell can still be judged, alone.
            dzi = None
        return {
            "check": _text(manifest.get("check"), 40),
            "provider": _text(source.get("provider"), 200),
            "version": _text(source.get("version"), 200),
            "gpu": _text(manifest.get("gpu"), 200),
            "rendered_at": rendered if isinstance(rendered, str) and ISO_TIME.fullmatch(rendered) else "",
            "pipeline": _text(manifest.get("pipeline"), 40),
            "side": side,
            "box": list(overlay_box(side)) if side else None,
            "map": dzi is not None and cell_tile(dzi, col, row) is not None,
        }

    def upload(self, sheet: str, col: int, row: int) -> bytes:
        try:
            data = api.download(f"/v1/inkorg/{sheet}/{col}_{row}.png", self._token, UPLOAD_MAX)
        except RutorError as exc:
            if exc.code == 2:
                raise _Answer(404, "nothing is uploaded for that cell") from exc
            raise
        why = not_plain(data)
        if why is not None:
            raise _Answer(502, f"the upload is not a plain picture: {why}", "not_plain")
        return data

    def _map(self, sheet: str) -> Dzi | None:
        with self._lock:
            known = self._maps.get(sheet)
        if known is not None and (known[0] is not None or time.monotonic() - known[1] < NO_MAP_SECONDS):
            return known[0]
        status, raw = api.fetch_public(f"{config.tiles_url()}/{sheet}.dzi", _FROM_THIS_MACHINE, DZI_MAX)
        dzi = parse_dzi(raw) if status == 200 else None
        with self._lock:
            self._maps[sheet] = (dzi, time.monotonic())
        return dzi

    def _edition(self, sheet: str) -> int:
        """The sheet's live edition, asked about at most once a minute. 0 is the base map."""
        with self._lock:
            known = self._editions.get(sheet)
        if known is not None and time.monotonic() - known[1] < EDITION_SECONDS:
            return known[0]
        try:
            status, raw = api.fetch_public(f"{config.tiles_url()}/karta-utgava.json", _FROM_THIS_MACHINE, EDITIONS_MAX)
            edition = edition_of(raw, sheet) if status == 200 else 0
        except RutorError:
            # Without an answer the last known edition stands. The tile itself says if the service is down.
            edition = known[0] if known is not None else 0
        with self._lock:
            self._editions[sheet] = (edition, time.monotonic())
        return edition

    def tile(self, sheet: str, col: int, row: int) -> bytes:
        """Today's map over one cell: the base, or the live edition when cells have been put in."""
        edition = self._edition(sheet)
        dzi = self._map(sheet)
        place = cell_tile(dzi, col, row) if dzi is not None else None
        if place is None:
            raise _Answer(404, "the map has no picture there")
        key = (sheet, edition, col, row)
        with self._lock:
            kept = self._tiles.get(key)
        if kept is not None:
            return kept
        level, x, y = place
        name = f"{sheet}@{edition}" if edition else sheet
        status, data = api.fetch_public(f"{config.tiles_url()}/{name}_files/{level}/{x}_{y}.webp", _FROM_THIS_MACHINE, TILE_MAX)
        stand_in = status == 404 and edition > 0
        if stand_in:
            # The editions file can be ahead of the tiles for a moment after a publication. The
            # base is shown meanwhile, and not kept, so the edition's own tile is asked for again.
            status, data = api.fetch_public(f"{config.tiles_url()}/{sheet}_files/{level}/{x}_{y}.webp", _FROM_THIS_MACHINE, TILE_MAX)
        if status == 404:
            raise _Answer(404, "the map has no picture there")
        if status != 200 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise _Answer(502, "the tile service did not answer with a picture")
        if stand_in:
            return data
        with self._lock:
            # Tiles never change under their address, so they are kept; the oldest go first.
            while len(self._tiles) >= TILES_KEPT:
                del self._tiles[next(iter(self._tiles))]
            self._tiles[key] = data
        return data

    def approve(self, raw: bytes) -> dict[str, object]:
        cell, _body = _cell_body(raw)
        api.post("/v1/approve", cell, self._token)
        return {"ok": True}

    def reject(self, raw: bytes) -> dict[str, object]:
        cell, body = _cell_body(raw)
        comment = body.get("comment")
        comment = comment.strip() if isinstance(comment, str) else ""
        if not comment or len(comment) > COMMENT_MAX:
            raise bad_input(f"a reason of 1 to {COMMENT_MAX} characters is needed")
        api.post("/v1/reject", {**cell, "comment": comment}, self._token)
        return {"ok": True}


# ---- the server


def _handler(review: _Review, key: str, host: str) -> type[BaseHTTPRequestHandler]:
    origin = f"http://{host}"

    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, format: str, *args: object) -> None:
            # The address holds the key, so requests are not logged.
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The browser stopped waiting, as it does when another cell is chosen.
                pass

        def _json(self, status: int, payload: object) -> None:
            self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

        def _inside(self) -> str | None:
            """The path after the key, or None once the request has been turned away."""
            if (self.headers.get("Host") or "") != host:
                self._json(403, {"error": "this page answers on this machine only"})
                return None
            path = self.path.split("?", 1)[0]
            parts = path.split("/", 2)
            if len(parts) != 3 or parts[0] != "" or not hmac.compare_digest(parts[1].encode(), key.encode()):
                self._json(404, {"error": "not found"})
                return None
            return parts[2]

        def _run(self, work: Callable[[], None]) -> None:
            try:
                work()
            except _Answer as exc:
                self._json(exc.status, {"error": exc.message, "code": exc.code} if exc.code else {"error": exc.message})
            except RutorError as exc:
                self._json(STATUS_FOR.get(exc.code, 500), {"error": api.one_line(exc.message)})
            except Exception as exc:  # noqa: BLE001 - the last stop before the socket
                # The kind of fault and where, for the terminal. Never the message: this
                # process holds the login and no fault text is trusted to leave it out.
                where = traceback.extract_tb(exc.__traceback__)[-1]
                print(f"review: {type(exc).__name__} at {Path(where.filename).name}:{where.lineno}", file=sys.stderr)
                self._json(500, {"error": "the page failed on this machine; see the terminal"})

        def do_GET(self) -> None:
            rest = self._inside()
            if rest is None:
                return
            self._run(lambda: self._get(rest))

        def _get(self, rest: str) -> None:
            page = PAGE_FILES.get(rest)
            if page is not None:
                self._send(200, page[1], (PAGE_DIR / page[0]).read_bytes())
                return
            if rest == "api/queue":
                self._json(200, review.queue())
                return
            for name, pattern in ROUTES.items():
                found = pattern.fullmatch(rest)
                if found is None:
                    continue
                sheet, col, row = found["sheet"], int(found["col"]), int(found["row"])
                if name == "facts":
                    self._json(200, review.facts(sheet, col, row))
                elif name == "upload":
                    self._send(200, "image/png", review.upload(sheet, col, row))
                else:
                    self._send(200, "image/webp", review.tile(sheet, col, row))
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            rest = self._inside()
            if rest is None:
                return
            sent_from = self.headers.get("Origin")
            if sent_from is not None and sent_from != origin:
                self._json(403, {"error": "this page takes no requests from other pages"})
                return
            if rest not in ("api/approve", "api/reject"):
                self._json(404, {"error": "not found"})
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self._json(415, {"error": "send JSON"})
                return
            length = self.headers.get("Content-Length") or ""
            if not (length.isascii() and length.isdigit()) or not 0 < int(length) <= BODY_MAX:
                self._json(400, {"error": "the body is missing or too large"})
                return
            raw = self.rfile.read(int(length))
            self._run(lambda: self._json(200, review.approve(raw) if rest == "api/approve" else review.reject(raw)))

        def _no(self) -> None:
            self._json(405, {"error": "GET and POST only"})

        do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no

    return Handler


class _OwnPort(ThreadingHTTPServer):
    # Nobody else may listen on the port while the page is up. On Windows the default lets them.
    allow_reuse_address = False

    def handle_error(self, request: object, client_address: object) -> None:
        # A browser drops a connection whenever another cell is chosen. That is not a fault,
        # and the standard report of it would print the address, which holds the key.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        print(f"review: {type(sys.exc_info()[1]).__name__} while answering", file=sys.stderr)


class ReviewServer:
    """The page on 127.0.0.1, under an address nobody can guess.

    The port is a free one, new at every start. A fixed port would give the page the same
    origin every time, and whatever an earlier program left in the browser under that origin
    would still be there.
    """

    def __init__(self, token: str) -> None:
        self.key = secrets.token_urlsafe(32)
        self._httpd = _OwnPort(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.port = int(self._httpd.server_address[1])
        self._httpd.RequestHandlerClass = _handler(_Review(token), self.key, f"127.0.0.1:{self.port}")
        self.url = f"http://127.0.0.1:{self.port}/{self.key}/"
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._httpd.server_close()


def cmd_review(args: argparse.Namespace) -> int:
    token = config.read_token()
    if not token:
        raise environment("not logged in: run rutor login")
    # Asked once before the page opens, so that a login that may not review hears it here.
    api.get("/v1/queue?state=vantar", token=token)
    try:
        server = ReviewServer(token)
    except OSError as exc:
        raise environment(f"could not open a port on this machine ({exc.strerror})") from exc
    print(f"url: {server.url}", flush=True)
    print("Whoever has that address can approve and reject cells. Keep it to yourself.")
    print("Ctrl-C closes the page.", flush=True)
    if not args.no_open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    print("closed")
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    review = sub.add_parser("review", aliases=["granska"], help="open the page for approving and rejecting uploaded cells")
    review.add_argument("--no-open", action="store_true", help="print the address and do not open a browser")
    review.set_defaults(func=cmd_review)
