"""Where rutor keeps the token, the service url, and the Google key."""

from __future__ import annotations

import os
from pathlib import Path

from rutor.errors import environment

DEFAULT_SERVICE_URL = "https://pixelbygget.gustaf-garnow.workers.dev"
PLACEHOLDER_CLIENT_ID = "REPLACE_WITH_CLIENT_ID"


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
    return os.environ.get("PIXELBYGGET_GITHUB_CLIENT_ID", PLACEHOLDER_CLIENT_ID)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def work_dir(sheet: str) -> Path:
    path = repo_root() / "work" / sheet
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env_assignment(text: str, name: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != name:
            continue
        return value.strip().strip('"').strip("'")
    return None


def read_env_key(root: Path) -> str:
    env_path = root / ".env"
    if not env_path.is_file():
        raise environment(
            "GOOGLE_MAPS_KEY is missing. Put it in .env in the clone and run chmod 600 .env."
        )
    if os.name == "posix":
        mode = env_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise environment(
                f".env is readable by other users (mode {mode:04o}). Run chmod 600 .env and try again."
            )
    key = _env_assignment(env_path.read_text(encoding="utf-8"), "GOOGLE_MAPS_KEY")
    if not key:
        raise environment("GOOGLE_MAPS_KEY is missing from .env. Add it and run chmod 600 .env.")
    return key
