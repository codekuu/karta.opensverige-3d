"""Where rutor keeps the token and the service url."""

from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

from rutor.errors import bad_input, environment

DEFAULT_SERVICE_URL = "https://pixelbygget.gustaf-garnow.workers.dev"
# The pixel maps' tiles. Only the review page reads them, to show a cell among its neighbours.
DEFAULT_TILES_URL = "https://pixelkarta.gustaf-garnow.workers.dev/v1"
# The "Pixelbygget" OAuth App under the opensverige organisation. A client id is public; the
# device flow needs no secret.
GITHUB_CLIENT_ID = "Ov23liulid4VoLnmJCVH"


def home() -> Path:
    override = os.environ.get("PIXELBYGGET_HOME")
    if override:
        return Path(override)
    return Path.home() / ".config" / "pixelbygget"


def token_path() -> Path:
    return home() / "token"


def read_token() -> str | None:
    path = token_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def write_token(token: str) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT honours umask, so a second chmod pins the mode at 0600.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, token.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAP_URL = "https://karta.opensverige.se/pixelbygget"
SHEET_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def _own_or_this_machine(name: str, default: str, refusal: str) -> str:
    """The project's own address, or one on this machine when the variable `name` says so."""
    raw = os.environ.get(name, "").strip()
    if not raw or raw.rstrip("/") == default:
        return default
    try:
        parts = urllib.parse.urlsplit(raw)
        host = parts.hostname or ""
        port_ok = parts.port is None or 0 < parts.port < 65536
    except ValueError:
        host, parts, port_ok = "", None, False
    if (
        parts is not None
        and port_ok
        and parts.scheme in ("http", "https")
        and host in LOOPBACK_HOSTS
        and not parts.username
        and not parts.password
    ):
        return raw.rstrip("/")
    raise environment(refusal)


def service_url() -> str:
    """The service the token is sent to: the project's own, or one on this machine for testing.

    PIXELBYGGET_URL cannot point anywhere else. The token proves who you are to the service, so
    an instruction to "set PIXELBYGGET_URL to the new address" would hand your identity to
    whoever wrote it. A fork that runs its own service changes DEFAULT_SERVICE_URL in code.
    """
    return _own_or_this_machine(
        "PIXELBYGGET_URL",
        DEFAULT_SERVICE_URL,
        "PIXELBYGGET_URL may only point at this machine (localhost) for testing. "
        "Unset it to use the project's service. Your login is never sent to another address.",
    )


def tiles_url() -> str:
    """Where the review page reads today's map from: the project's tile service, or this machine.

    No login is sent there. The rule is the same as for the service all the same, so that no
    instruction can make the maintainer's machine fetch pictures from an address of its choosing.
    """
    return _own_or_this_machine(
        "PIXELBYGGET_TILES_URL",
        DEFAULT_TILES_URL,
        "PIXELBYGGET_TILES_URL may only point at this machine (localhost) for testing. "
        "Unset it to use the project's tile service.",
    )


def sheet_id(text: str) -> str:
    """A sheet id as the service names it. It becomes a folder name and part of a URL."""
    if not isinstance(text, str) or not SHEET_ID.fullmatch(text):
        raise bad_input("sheet must look like goteborg-norr: lower case letters, digits and hyphens")
    return text


def github_client_id() -> str:
    return os.environ.get("PIXELBYGGET_GITHUB_CLIENT_ID", GITHUB_CLIENT_ID)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def work_dir(sheet: str) -> Path:
    path = repo_root() / "work" / sheet_id(sheet)
    path.mkdir(parents=True, exist_ok=True)
    return path


