"""Config: token file, service url, and the Google key in .env."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from rutor.config import read_env_key, read_token, service_url, write_token
from rutor.errors import RutorError


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = {name: os.environ.get(name) for name in ("PIXELBYGGET_HOME", "PIXELBYGGET_URL")}
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ.pop("PIXELBYGGET_URL", None)

    def tearDown(self) -> None:
        for name, value in self._old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def test_token_roundtrip_is_0600(self) -> None:
        write_token("github-token")
        path = Path(self.tmp.name) / "token"
        self.assertEqual(read_token(), "github-token")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_service_url_default_and_override(self) -> None:
        self.assertEqual(service_url(), "https://pixelbygget.gustaf-garnow.workers.dev")
        os.environ["PIXELBYGGET_URL"] = "http://127.0.0.1:9/"
        self.assertEqual(service_url(), "http://127.0.0.1:9")

    def test_read_env_key_reads_GOOGLE_MAPS_KEY(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        env = root / ".env"
        env.write_text('GOOGLE_MAPS_KEY="map-key"\n', encoding="utf-8")
        os.chmod(env, 0o600)
        self.assertEqual(read_env_key(root), "map-key")

    def test_world_readable_env_refused(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        env = root / ".env"
        env.write_text("GOOGLE_MAPS_KEY=map-key\n", encoding="utf-8")
        os.chmod(env, 0o644)
        with self.assertRaises(RutorError) as caught:
            read_env_key(root)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("chmod 600 .env", caught.exception.message)

    def test_missing_env_is_environment_error(self) -> None:
        root = Path(self.tmp.name) / "empty"
        root.mkdir()
        with self.assertRaises(RutorError) as caught:
            read_env_key(root)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("GOOGLE_MAPS_KEY", caught.exception.message)
