"""PNG size, sha256 and the upload manifest."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rutor.errors import RutorError
from rutor.manifest import build_manifest, encode_manifest, png_size, sha256_file
from tests.png import minimal_png


class ManifestTests(unittest.TestCase):
    def test_png_size_reads_ihdr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cell.png"
            path.write_bytes(minimal_png(1536, 1024))
            self.assertEqual(png_size(path), (1536, 1024))

    def test_png_size_rejects_not_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_bytes(b"not a png")
            with self.assertRaises(RutorError) as caught:
                png_size(path)
            self.assertEqual(caught.exception.code, 1)

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bytes.bin"
            path.write_bytes(b"abc")
            self.assertEqual(sha256_file(path), hashlib.sha256(b"abc").hexdigest())

    def test_build_manifest_has_all_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cell.png"
            path.write_bytes(minimal_png(1536, 1536))
            manifest = build_manifest(
                pipeline="2026.11-a",
                seed=7,
                png=path,
                source={"provider": "google", "version": "2026-11"},
                gpu="RTX 5070",
                rendered_at="2026-11-17T12:00:00Z",
            )
            self.assertEqual(
                set(manifest),
                {"pipeline", "seed", "sha256", "width", "height", "source", "gpu", "renderedAt"},
            )
            self.assertEqual(manifest["width"], 1536)
            self.assertEqual(manifest["height"], 1536)
            self.assertEqual(manifest["seed"], 7)
            self.assertEqual(manifest["sha256"], sha256_file(path))

    def test_encode_manifest_is_base64_json(self) -> None:
        manifest = {"pipeline": "2026.11-a", "seed": None, "width": 1536}
        encoded = encode_manifest(manifest)
        self.assertEqual(json.loads(base64.b64decode(encoded)), manifest)
