"""In-process stand-in for the rut-tjänst.

`state` holds `sheets`, `cells` keyed `sheet/col_row`, `users`, plus `queue`,
`inbox` and `user` for the routes the CLI calls. `fail_next_with` answers the
next request with a fixed status and body.

`state["place"]` is what GET /v1/place answers, whatever point is asked: a dict
with `sheet`, `col`, `row` and `state`. Without it every point lies outside.
A sheet whose row says `made: "grid"` has free cells with no row, as in the
service: a claim inside its size makes the cell.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPIRES = "2026-11-19T12:00:00Z"


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


class FakeService:
    def __init__(self) -> None:
        self.state: dict[str, object] = {
            "sheets": {},
            "cells": {},
            "users": {},
            "queue": [],
            "inbox": {},
            "user": {"login": "klang", "github_id": 4242, "trusted": 0, "approved_count": 0},
        }
        self.requests: list[dict[str, object]] = []
        self._fail: tuple[int, bytes, str] | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def fail_next_with(self, status: int, body: bytes, content_type: str) -> None:
        self._fail = (status, body, content_type)

    def start(self) -> str:
        from unittest.mock import patch

        self._machine = patch("rutor.commands._gpu_missing", return_value=None)
        self._machine.start()
        handler = _handler(self)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        machine = getattr(self, "_machine", None)
        if machine is not None:
            machine.stop()
            self._machine = None
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def handle(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes, str]:
        self.requests.append({"method": method, "path": path, "headers": dict(headers), "body": body})
        if self._fail is not None:
            status, payload, content_type = self._fail
            self._fail = None
            return status, payload, content_type
        parsed = urllib.parse.urlsplit(path)
        parts = [part for part in parsed.path.split("/") if part]
        query = urllib.parse.parse_qs(parsed.query)
        if len(parts) < 2 or parts[0] != "v1":
            return _error(404, "not_found", "No such route.")
        head = parts[1]
        if method == "GET" and head == "health" and len(parts) == 2:
            return _json(200, {"ok": True, "service": "pixelbygget"})
        if method == "GET" and head == "sheets" and len(parts) == 2:
            return _json(200, {"sheets": list(_maps(self.state.get("sheets")).values())})
        if method == "GET" and head == "grid" and len(parts) == 3:
            return self._grid(parts[2])
        if method == "GET" and head == "place" and len(parts) == 2:
            place = _as_dict(self.state.get("place"))
            if place is None:
                return _error(404, "outside", "That point lies outside Sweden.")
            return _json(200, place)
        if method == "GET" and head == "me" and len(parts) == 2:
            denied = _auth(headers)
            if denied is not None:
                return denied
            user = _user(self.state)
            return _json(200, {**user, "locks": self._locks()})
        if method == "POST" and head == "claim":
            return self._claim(body, headers)
        if method == "POST" and head == "release":
            return self._release(body, headers)
        if method == "PUT" and head == "upload" and len(parts) == 4:
            return self._upload(parts[2], parts[3], headers)
        if method == "GET" and head == "queue":
            denied = _auth(headers)
            if denied is not None:
                return denied
            wanted = query.get("state", ["vantar"])[0]
            if wanted not in ("vantar", "godkand"):
                return _error(400, "bad_state", "state must be vantar or godkand.")
            return _json(200, {"state": wanted, "cells": self._queue(wanted)})
        if method == "POST" and head in ("approve", "reject", "trust"):
            denied = _auth(headers)
            if denied is not None:
                return denied
            return _json(200, {"ok": True})
        if method == "GET" and head == "inkorg":
            denied = _auth(headers)
            if denied is not None:
                return denied
            return self._inkorg("/".join(parts[2:]))
        return _error(404, "not_found", "No such route.")

    def _grid(self, sheet_id: str) -> tuple[int, bytes, str]:
        sheet = _maps(self.state.get("sheets")).get(sheet_id)
        if sheet is None:
            return _error(404, "no_sheet", f"No sheet named {sheet_id}.")
        rows: list[list[object]] = []
        for key, cell in _maps(self.state.get("cells")).items():
            if not key.startswith(sheet_id + "/"):
                continue
            rows.append([cell.get("col"), cell.get("row"), cell.get("state"), cell.get("login"), cell.get("updated")])
        rows.sort(key=lambda item: (int(item[1]) if isinstance(item[1], int) else 0, int(item[0]) if isinstance(item[0], int) else 0))
        return _json(200, {"sheet": sheet, "cells": rows})

    def _locks(self) -> list[dict[str, object]]:
        login = _user(self.state).get("login")
        found: list[dict[str, object]] = []
        for key, cell in _maps(self.state.get("cells")).items():
            if cell.get("state") != "last" or cell.get("login") != login:
                continue
            sheet, _, _cell_id = key.partition("/")
            found.append(
                {
                    "sheet": sheet,
                    "col": cell.get("col"),
                    "row": cell.get("row"),
                    "expires_at": cell.get("expires_at"),
                    "state": "last",
                }
            )
        return found

    def _claim(self, body: bytes, headers: dict[str, str]) -> tuple[int, bytes, str]:
        denied = _auth(headers)
        if denied is not None:
            return denied
        parsed = _cell_body(body)
        if parsed is None:
            return _error(400, "bad_body", "Send JSON with sheet, col and row.")
        sheet_id, col, row = parsed
        sheet = _maps(self.state.get("sheets")).get(sheet_id)
        if sheet is None:
            return _error(404, "no_sheet", "No such sheet.")
        if sheet.get("state") != "open":
            return _error(403, "sheet_closed", "That sheet is not open for claims.")
        cells = _maps(self.state.get("cells"))
        cell = cells.get(f"{sheet_id}/{col}_{row}")
        if cell is None and sheet.get("made") == "grid" and _inside(sheet, col, row):
            # A sheet of the tiling: the first claim on a cell makes its row.
            cell = {"col": col, "row": row, "state": "fri", "login": None, "updated": "", "expires_at": None}
            stored = self.state.get("cells")
            if isinstance(stored, dict):
                stored[f"{sheet_id}/{col}_{row}"] = cell
        if cell is None:
            return _error(404, "no_cell", "No such cell.")
        if cell.get("state") == "ute":
            return _error(422, "outside", "That cell lies outside the city and is not drawn.")
        user = _user(self.state)
        limit = 8 if user.get("trusted") else 1
        if len(self._locks()) >= limit:
            return _error(403, "too_many_locks", "You already hold as many cells as you may. Finish or release one first.")
        if cell.get("state") != "fri":
            return _error(409, "taken", "Someone else holds that cell. Pick another.")
        cell["state"] = "last"
        cell["login"] = user.get("login")
        cell["expires_at"] = EXPIRES
        return _json(201, {"sheet": sheet_id, "col": col, "row": row, "expires_at": EXPIRES})

    def _release(self, body: bytes, headers: dict[str, str]) -> tuple[int, bytes, str]:
        denied = _auth(headers)
        if denied is not None:
            return denied
        parsed = _cell_body(body)
        if parsed is None:
            return _error(400, "bad_body", "Send JSON with sheet, col and row.")
        sheet_id, col, row = parsed
        cell = _maps(self.state.get("cells")).get(f"{sheet_id}/{col}_{row}")
        if cell is None:
            return _error(404, "no_cell", "No such cell.")
        user = _user(self.state)
        if cell.get("login") != user.get("login"):
            return _error(403, "not_yours", "That cell is not yours.")
        if cell.get("state") != "last":
            return _error(409, "already_uploaded", "That cell is already uploaded and cannot be released.")
        cell["state"] = "fri"
        cell["login"] = None
        cell["expires_at"] = None
        return _json(200, {"ok": True})

    def _upload(self, sheet_id: str, cell_id: str, headers: dict[str, str]) -> tuple[int, bytes, str]:
        denied = _auth(headers)
        if denied is not None:
            return denied
        return _json(
            201,
            {"state": "vantar", "url": f"https://karta.opensverige.se/pixelbygget#{sheet_id}/{cell_id}"},
        )

    def _queue(self, state: str) -> list[object]:
        raw = self.state.get("queue")
        if not isinstance(raw, list):
            return []
        found: list[object] = []
        for item in raw:
            parsed = _as_dict(item)
            if parsed is None:
                continue
            if parsed.get("state", "vantar") == state:
                found.append(parsed)
        return found

    def _inkorg(self, key: str) -> tuple[int, bytes, str]:
        inbox = self.state.get("inbox")
        if isinstance(inbox, dict):
            payload = inbox.get(key)
            if isinstance(payload, bytes):
                return 200, payload, "image/png"
        return _error(404, "not_found", "Nothing in the inbox under that name.")


def _maps(value: object) -> dict[str, dict[str, object]]:
    # The inner dicts are the ones stored in `state`, not copies, so a claim
    # updates the cell the test inserted.
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, dict):
            out[key] = item
    return out


def _inside(sheet: dict[str, object], col: int, row: int) -> bool:
    cols, rows = sheet.get("cols"), sheet.get("rows")
    return isinstance(cols, int) and isinstance(rows, int) and 0 <= col < cols and 0 <= row < rows


def _user(state: dict[str, object]) -> dict[str, object]:
    parsed = _as_dict(state.get("user"))
    if parsed is None:
        return {"login": "klang", "github_id": 4242, "trusted": 0, "approved_count": 0}
    return parsed


def _auth(headers: dict[str, str]) -> tuple[int, bytes, str] | None:
    for key, value in headers.items():
        if key.lower() == "authorization" and value.lower().startswith("bearer "):
            return None
    return _error(401, "no_token", "Log in first: rutor login.")


def _cell_body(body: bytes) -> tuple[str, int, int] | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    payload = _as_dict(parsed)
    if payload is None:
        return None
    sheet = payload.get("sheet")
    col = payload.get("col")
    row = payload.get("row")
    if not isinstance(sheet, str) or not isinstance(col, int) or not isinstance(row, int):
        return None
    return sheet, col, row


def _error(status: int, code: str, message: str) -> tuple[int, bytes, str]:
    return _json(status, {"error": code, "message": message})


def _json(status: int, payload: object) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json"


def _handler(service: FakeService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _go(self) -> None:
            length_raw = self.headers.get("Content-Length", "0")
            length = int(length_raw) if length_raw else 0
            body = self.rfile.read(length) if length else b""
            headers = {str(key): str(value) for key, value in self.headers.items()}
            status, payload, content_type = service.handle(self.command, self.path, body, headers)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._go()

        def do_POST(self) -> None:
            self._go()

        def do_PUT(self) -> None:
            self._go()

    return Handler
