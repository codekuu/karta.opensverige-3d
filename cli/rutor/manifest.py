"""PNG measurements and the manifest the service reads from X-Manifest."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path

from rutor.errors import bad_input

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IHDR = b"IHDR"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != IHDR:
        raise bad_input(f"{path.name} is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def build_manifest(
    *,
    pipeline: str,
    seed: int | None,
    png: Path,
    source: dict[str, str],
    gpu: str,
    rendered_at: str,
) -> dict[str, object]:
    width, height = png_size(png)
    return {
        "pipeline": pipeline,
        "seed": seed,
        "sha256": sha256_file(png),
        "width": width,
        "height": height,
        "source": source,
        "gpu": gpu,
        "renderedAt": rendered_at,
    }


def encode_manifest(manifest: dict[str, object]) -> str:
    raw = json.dumps(manifest, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def write_manifest(manifest: dict[str, object], path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
