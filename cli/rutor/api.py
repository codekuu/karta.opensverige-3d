"""HTTP client for the rut-tjänst. Stdlib only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from rutor import __version__
from rutor.config import service_url
from rutor.errors import network, refused

TIMEOUT_SECONDS = 30


def _exchange(
    method: str,
    path: str,
    *,
    token: str | None,
    body: bytes | None,
    json_body: object | None,
    headers: dict[str, str] | None,
) -> tuple[int, bytes]:
    hdrs = {
        "User-Agent": f"rutor/{__version__}",
        "Accept": "application/json",
    }
    data = body
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if headers:
        hdrs.update(headers)
    # HTTPError is a URLError. It has to be caught first so a 409 stays a refusal
    # and only a dead connection becomes a network error.
    request = urllib.request.Request(service_url() + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
            return int(exc.code), payload
        finally:
            exc.close()
    except urllib.error.URLError as exc:
        raise network(f"could not reach the service ({exc.reason})") from exc


def _parse(status: int, raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise network(f"service answered {status}, not JSON") from exc


def _refuse(status: int, parsed: object) -> None:
    code = f"http_{status}"
    message = ""
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, str):
            code = error
        found = parsed.get("message")
        if isinstance(found, str):
            message = found
    raise refused(f"refused: {code}: {message}")


def request(
    method: str,
    path: str,
    *,
    token: str | None,
    body: bytes | None = None,
    json_body: object | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object]:
    status, raw = _exchange(method, path, token=token, body=body, json_body=json_body, headers=headers)
    parsed = _parse(status, raw)
    if status >= 400:
        _refuse(status, parsed)
    return status, parsed


def get(path: str, token: str | None = None) -> tuple[int, object]:
    return request("GET", path, token=token)


def post(path: str, json_body: object, token: str | None) -> tuple[int, object]:
    return request("POST", path, token=token, json_body=json_body)


def put_bytes(path: str, data: bytes, headers: dict[str, str], token: str | None) -> tuple[int, object]:
    return request("PUT", path, token=token, body=data, headers=headers)


def download(path: str, token: str | None) -> bytes:
    status, raw = _exchange(method="GET", path=path, token=token, body=None, json_body=None, headers={"Accept": "*/*"})
    if status >= 400:
        _refuse(status, _parse(status, raw))
    return raw
