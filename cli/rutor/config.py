"""Where rutor keeps the token and the service url."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_SERVICE_URL = "https://pixelbygget.gustaf-garnow.workers.dev"
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


def service_url() -> str:
    raw = os.environ.get("PIXELBYGGET_URL", DEFAULT_SERVICE_URL)
    return raw.rstrip("/")


def github_client_id() -> str:
    return os.environ.get("PIXELBYGGET_GITHUB_CLIENT_ID", GITHUB_CLIENT_ID)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def work_dir(sheet: str) -> Path:
    path = repo_root() / "work" / sheet
    path.mkdir(parents=True, exist_ok=True)
    return path


